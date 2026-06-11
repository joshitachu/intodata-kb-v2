---
feature: Track A — doc-generation artifact and dead-code reduction
slice: Slice 5 — dead JSON maps + audit-tree manifest + docs + acceptance run (FR-2, FR-7, FR-10, FR-11)
sub_project: knowledge_base
critical_area: yes — harness pipeline phases + MCP API consumers
verdict: PASS
approved_at: 2026-06-10
---

## Problem Statement

Two feature requirements from the Slice 4 scope:

**FR-6 (transient dir consolidation)**: Five distinct transient directories scatter across
`docs_staging/` — `_context_slices/` and `_assessment/_scorecard_reduce/` accumulate
without cleanup; `_analysis/_merge_inputs/`, `_analysis/_synthesis_inputs/`,
`_analysis/_repair_context/` disappear only because `_analysis/` is deleted by Phase 4.
These dirs exist solely to bridge data between adjacent steps within one phase; no later
phase, validator, gate, resume path, or API route reads them.

**FR-8 (prompt corpus dedup)**: `materialize_prompt_packs()` writes assembled packs to
`_assessment/prompt_packs/`. Harness.py retains fallback logic reading individual source
files from `_preprocess/` when packs are absent. Since packs are always materialized in
Phase 1, fallback branches in `_phase3_doc_context_files` (the three-way ref-glob path)
and `phase26_scorecard.py` are dead code with a second on-disk copy of the prompt corpus.

## Current State (pre-build)

**Already implemented in uncommitted working tree** (verified by code inspection):

- `transient_artifacts.py` created with `transient_dir()` and `transient_write()` helpers
- `harness.py`: all 5 transient write paths converted to `transient_dir()` / `transient_write()`
  - `_phase2_merge_agent`: `_analysis/_merge_inputs/` → `_transient/`
  - `_phase2_cross_chunk_synthesis`: `_analysis/_synthesis_inputs/` → `_transient/`
  - `_reduce_split_scorecard`: `_assessment/_scorecard_reduce/` → `_transient/`
  - `_phase3_repair_context_files`: `_analysis/_repair_context/` → `_transient/`
  - `_split_file`: `_context_slices/` → `_transient/context_slices/`
- `phase4_cleanup.py`: `_transient/` added to cleanup list alongside `_preprocess/` and `_analysis/`
- `prompt_packs.py`: `_phase2_sources` simplified; `harness.py` new `_phase2_analysis_guide_files`
  static helper consolidates the guide resolution logic
- `json_writer.py`: 9 dead JSON maps removed (FR-2 — Slice 5 scope, delivered early)
- `knowledge_base/CLAUDE.md` + `mcp-workflow.md`: docs rewritten (FR-10 — Slice 5 scope, delivered early)

**Still needed** (remaining FR-8 gap):

- `harness.py` `_phase3_doc_context_files` (~lines 4671–4701): three-way fallback block
  that reads `_phase3_guide.md`, `_doc_templates.md`, `_source_coverage.json`, and
  `_ref_*.md` from `_preprocess/` when the phase3 prompt pack is absent — dead code since
  packs are always materialized before Phase 3
- `phase26_scorecard.py` (~lines 68–75): `else` branch reading `SCORECARD_SOURCE_FILES`
  directly from `_preprocess/` when `scorecard_pack` is absent — same dead-code status

## Proposed Solution

Remove the two remaining dead fallback blocks. Both are reachable only when
`materialize_prompt_packs()` failed to write packs. Since it always runs in Phase 1 before
Phase 2/2.6/3 executes, these branches never fire in production. Removing them gives the
prompt corpus a single authoritative location (`_assessment/prompt_packs/`) and eliminates
the implicit dependency on raw `_preprocess/` prompt files in Phase 3/2.6 context assembly.

Keep the `logger.warning` at line 4672 so a missing pack surfaces as a warning instead of
silently producing empty context. Remove only the file-append blocks after the warning.

## Affected Files

Build work needed:
- `mcp-server/src/mcp_server/providers/custom/harness.py`
  — remove dead ref-glob block (~lines 4691–4701)
  — remove dead guide/template fallback body (~lines 4673–4678)
- `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase26_scorecard.py`
  — remove `else` branch (~lines 68–75) reading SCORECARD_SOURCE_FILES from `_preprocess/`

Already complete (no further edits needed):
- `transient_artifacts.py` (new, done)
- `harness.py` FR-6 sites (done)
- `phase4_cleanup.py` (done)
- `prompt_packs.py` (done)
- `json_writer.py` (done, FR-2 early)
- `knowledge_base/CLAUDE.md` + `mcp-workflow.md` (done, FR-10 early)

## Architecture Contracts

- LLM-input invariance (PRD Goal 2): fallback branches only fire when packs are absent;
  packs are always written in Phase 1. Removing dead branches does not change what any LLM
  call receives.
- FR-6 keep-list: `_model_slices/` untouched (resume cache). `_transient/` is explicitly
  not in the resume keep-list.
- No new flags, no API changes, no schema changes.
- knowledge_base/CLAUDE.md rule 5 (no intermediate files in final output): transient dirs
  are truly intermediate and correctly cleaned up by Phase 4.

## Implementation Steps

1. **Remove dead three-way fallback body in `_phase3_doc_context_files`** (harness.py):

   Current structure:
   ```
   if prompt_pack.exists():
       files.append(prompt_pack)
   else:
       logger.warning(...)       ← KEEP
       phase3_guide ...          ← REMOVE
       doc_templates ...         ← REMOVE
   ...
   if prompt_pack.exists():      ← REMOVE this entire block (lines ~4691-4701)
       return files
   if source_coverage.exists():  ← REMOVE
       files.append(source_coverage)
   if bundle == "core":          ← REMOVE ref-glob block
       ...
   ```
   After: `else` body contains only the `logger.warning` call; the early-exit
   block at ~4691 and the ref-glob block below it are deleted.

2. **Remove dead fallback in `phase26_scorecard.py`**:

   Remove the `else` branch (lines 68–75) that calls `self._helpers.existing(...)`.
   Keep only: `if scorecard_pack.exists(): context_files.append(scorecard_pack)`.

3. **Grep gates**:
   - `grep -n "_ref_core\|_ref_business\|_ref_patterns\|_ref_user" harness.py` →
     zero results outside `LARGE_CONTEXT_ARTIFACTS` or unrelated comments
   - `grep -n "_scoring_rubric\|_anti_patterns\|_red_flags\|_security_checklist\|_pattern_library" harness.py phase26_scorecard.py` →
     zero results
   - `grep -n "_context_slices\|_merge_inputs\|_synthesis_inputs\|_scorecard_reduce" harness.py` →
     zero results

4. **Full test suite**: `uv run python -m pytest tests/ -x -q` → 590+ passed.

## Verification Strategy

- Suite green at 590+ passed.
- Grep gates (step 3) all zero hits.
- No existing test asserts the presence of `_phase3_guide.md` or `_ref_*.md` in
  Phase 3 context files — confirm by checking `test_custom_provider.py` for any
  such assertion before removing.
- Edge case: no `scorecard_pack` in phase26_scorecard → no crash; `context_files`
  still includes `investigation_files` and `analysis_files`.
- Edge case: no phase3 prompt pack → `logger.warning` fires, `files` still includes
  `doc_pack`, `evidence_file`, `manifest`, and `obligations` — doc generation proceeds
  without prompt guide context.

## Out of Scope

- FR-7 `_audit/` reshape — Slice 5 (remaining work)
- FR-11 `test_audit_bundle_only_writes_gates_and_hashes` + `_load_enterprise_gate_results`
  fallback test + acceptance run — Slice 5
- FR-2 + FR-10: already delivered in this working tree, will commit with this slice
- The four audit bugs (validator parentheses, incremental-regen cache, preamble
  stripping, scorecard weight mismatch) — separate work
- Any LLM-input change (Track B)

- **Nine root JSON maps** are written by `JsonFactWriter` for every run but
  have zero consumers anywhere in the codebase. Each is 100% duplicated inside
  the `engineering_facts.json` aggregate (which is the only live consumer of
  the same data). The maps: `entity_index.json`, `usage_index.json`,
  `dependency_map.json`, `architecture_map.json`, `data_flow_map.json`,
  `api_surface.json`, `known_risks.json`, `test_coverage_map.json`,
  `change_impact_matrix.json`. They are also the source of the `dependency_map.md`
  / `architecture_map.md` / etc. (the live scoped-tool corpus) which stay.
- **`_audit/` carries wholesale recursive copies** of `_assessment/`, `_analysis/`,
  `_investigation/`, plus copies of `facts_manifest.json`,
  `documentation_obligations.json`, and `provider_calls.json`. None of those
  copies is read by any REST route, gate, validator, or downstream service.
  The only thing `_audit/` is actually read for is the
  `_load_enterprise_gate_results` REST fallback at `projects.py:786`, which
  consumes `_audit/enterprise_gates.json` (synthesized, not copied).
- **Docs reference removed modules and stale artifact claims.** `mcp-workflow.md`
  has a dedicated "Evidence And Answerability Artifacts" section listing the
  four files deleted in Slice 1, plus a Phase 2.8 description that says it
  writes `_preprocess/` (it actually writes `_analysis/_doc_pack_*.md`).
  `CLAUDE.md` lists `source_fact_ledger.json`/`evidence_retention.json`/
  `repair/*.json`/`answerability_report.json` as live artifacts. Neither file
  documents `MCP_PIPELINE_DEBUG` or the `_debug/` layout.

## Proposed Solution

Three coordinated one-commit changes plus a paired-run acceptance check.

**FR-2 (JSON maps).** Prune `JSON_ARTIFACTS` in `engineering_facts/writers/json_writer.py`
from 10 entries to 1 (`config_runtime_map`); the only JSON-per-artifact file
that has a real reader (`validators.py:1559-1564` `scanned_config_files`).
The other 9 fields stay on the Pydantic model (they still serialize into the
`engineering_facts.json` aggregate that the SQL gate reads), but their
per-artifact `.json` twins are no longer written. Shrink `LARGE_CONTEXT_ARTIFACTS`
in `harness.py:106-118` from 11 entries to 2 (`engineering_facts.json` +
`config_runtime_map.json`) — both still dropped from LLM context, but the
other 9 entries are now redundant with the file no longer existing. Update two
test files that assert `entity_index.json` exists.

**FR-7 (audit tree).** Replace `_write_audit_bundle` body
(`harness.py:3976-3999`) to write only the two synthesized files: mkdir
`_audit/`, write `enterprise_gates.json` (already passed in as the
`gate_report` param), write `final_doc_hashes.json` (computed SHA-256 of
`staging/*.md`). Drop the three file-copy lines and the three dir-copy lines.
The REST fallback at `projects.py:783-794` continues to work because it tries
`_assessment/enterprise_gates.json` first (the primary write, unchanged at
`harness.py:3910`) and falls back to `_audit/enterprise_gates.json`. The
fallback remains reliable because `_audit/` is not in the Phase 4 rmtree
list (`phase4_cleanup.py:76-79` deletes only `_preprocess`, `_analysis`,
`_transient`) and survives the atomic swap via `services/atomic_swap.py`.

**FR-10 (docs).** Three edits to `knowledge_base/CLAUDE.md` and
`knowledge_base/.agent/system/mcp-workflow.md`:

- `mcp-workflow.md`: delete the "Evidence And Answerability Artifacts" section
  (lines 29-44), the `AnswerabilityAuditor` paragraph (line 115-118), the
  `_assessment/source_fact_ledger.json`/`evidence_retention.json`/
  `answerability_report.json` rows in the artifacts table; fix the Phase 2.8
  description (line 18) and the input-flow paragraph (lines 24-26) to say
  `_analysis/_doc_pack_*` not `_preprocess/`; add a new short section
  documenting `MCP_PIPELINE_DEBUG` and the `_debug/` layout.
- `CLAUDE.md`: drop the four removed artifacts from the Current Custom Harness
  Notes list (lines 76-80); update line 81 task-dir contents (only `status.json`
  + `output.md` are always-on; the other four files are debug-gated and only
  present under `MCP_PIPELINE_DEBUG=1`); add a one-liner documenting
  `MCP_PIPELINE_DEBUG`.

**FR-11 (tests + acceptance).** Two unit-test changes + a paired-run acceptance
check:

- `test_builder_writers.py:59-60` and `test_service.py:38` — drop the
  `entity_index.json` existence assertions; assert that the 9 dead `.json` files
  are not written.
- Add `test_audit_bundle_only_writes_gates_and_hashes` — a new test that runs
  `_write_audit_bundle` (or its replacement) with a populated staging dir and
  asserts `_audit/` contains exactly `enterprise_gates.json` and
  `final_doc_hashes.json`. This pins the FR-7 invariant.
- Add `test_load_enterprise_gate_results_falls_back_to_audit` — a new test
  that exercises `projects._load_enterprise_gate_results` with only
  `_audit/enterprise_gates.json` present and asserts the parsed gate report is
  returned. This pins the FR-7 REST-fallback invariant.
- Paired-run acceptance (manual, not a unit test): on the reference project
  (LobsterProfileExportKoopmansTest, default run), capture (a) the staging
  file count and total bytes, (b) per-call `_model_inputs/**/manifest.json`
  context file lists and sizes. Apply Slice 5. Re-run. Assert: file count
  ≤ 300, total bytes ≤ 20 MB, per-call manifest diff is empty (LLM-input
  invariance). The "byte-identical docs" criterion is documented as
  conditional on identical recorded model responses (replay path); the
  measurable invariant is the per-call manifest diff.

Grounded write-site inventory (current line numbers, post-Slices 1-4):

`engineering_facts/writers/json_writer.py`:
- `JSON_ARTIFACTS` dict at :14-25 (10 entries — the 9 dead + `config_runtime_map`)
- `JsonFactWriter.write()` at :31-44 — iterates `JSON_ARTIFACTS` and writes
  per-artifact files; aggregate `engineering_facts.json` is written regardless
  at :34-35

`harness.py`:
- `LARGE_CONTEXT_ARTIFACTS` at :106-118 (11 entries — the 9 dead + aggregate + `config_runtime_map.json`)
- `_write_audit_bundle` at :3976-3999 (24 lines: 3 file copies + 3 dir copies + 2 synthesized writes)
- `_enterprise_gate_report` writes `_assessment/enterprise_gates.json` at :3910 (KEEP)

`evidence_bundles.py:99-113` (`_base_files`) — **no edit**, already references
only live artifacts (`engineering_facts.json`, `sql_usage.json`,
`config_runtime_map.json`, `.md` siblings, `_preprocess/_source_coverage.json`).

`validators.py:1559-1564` (`scanned_config_files` candidates) — **no edit**,
already references only `config_runtime_map.json` and `engineering_facts.json`
plus their `_facts/` twins.

`api/routes/projects.py:783-794` (`_load_enterprise_gate_results`) — **no edit**,
already has the `_audit/` fallback that FR-7 requires.

`phase4_cleanup.py:76-79` — **no edit**, rmtree loop unchanged (does not
delete `_audit/`).

`tests/test_providers/test_engineering_facts/test_builder_writers.py:59-60` —
drop `entity_index.json` from the asserted set and from the read assertion.
`tests/test_providers/test_engineering_facts/test_service.py:38` — drop
`entity_index.json` existence assertion.

`knowledge_base/CLAUDE.md` — drop removed-artifact bullets (lines 76-80);
update task-dir contents bullet (line 81); add `MCP_PIPELINE_DEBUG` one-liner.

`knowledge_base/.agent/system/mcp-workflow.md` — delete the "Evidence And
Answerability Artifacts" section (lines 29-44), the AnswerabilityAuditor
paragraph (lines 115-118), and the three artifact-table rows; fix Phase 2.8
description (line 18) and input-flow paragraph (lines 24-26); add a new
short section documenting `MCP_PIPELINE_DEBUG` and `_debug/`.

## Affected Files

- `mcp-server/src/mcp_server/providers/custom/engineering_facts/writers/json_writer.py` (prune `JSON_ARTIFACTS`)
- `mcp-server/src/mcp_server/providers/custom/harness.py` (shrink `LARGE_CONTEXT_ARTIFACTS`; reduce `_write_audit_bundle` body)
- `mcp-server/tests/test_providers/test_engineering_facts/test_builder_writers.py` (drop dead-file assertions)
- `mcp-server/tests/test_providers/test_engineering_facts/test_service.py` (drop dead-file assertion)
- `mcp-server/tests/test_providers/test_custom_provider.py` (add 2 new tests for FR-7 invariants)
- `knowledge_base/CLAUDE.md` (drop removed artifacts; document debug flag)
- `knowledge_base/.agent/system/mcp-workflow.md` (drop Evidence And Answerability section; fix Phase 2.8 description; document debug flag)

## Architecture Contracts

- **REST fallback at `projects.py:786` keeps working.** `_audit/enterprise_gates.json`
  remains written (FR-7) so the fallback continues to serve gate results after
  the atomic swap (when the primary `_assessment/enterprise_gates.json` has
  been renamed into `docs/`).
- **`engineering_facts.json` aggregate is still written** (it's the SQL gate
  reader's source of truth at `harness.py:4391-4394`). Only the 9
  per-artifact twins are removed.
- **No LLM-input change.** The 9 dead `.json` files were never sent to the
  model (they're in the `LARGE_CONTEXT_ARTIFACTS` drop list). Removing the
  writes does not change any per-call `manifest.json` content. LLM-input
  invariance is automatic and the acceptance run will confirm it.
- **No new config flags.** `MCP_PIPELINE_DEBUG` already exists from Slice 2.
- **Phase 4 rmtree loop stays at `_preprocess`, `_analysis`, `_transient`.**
  `_audit/` is not touched, ensuring the REST fallback survives the swap.
- **The "byte-identical final docs" criterion is conditional on identical
  recorded model responses** (deterministic replay). The measurable
  LLM-input-invariance check is the per-call `manifest.json` diff.
- **Doc catalog and prompt templates are untouched.** No prompt text changes.
- **knowledge_base/CLAUDE.md rule 5 (no intermediate files in final output)**
  — this slice strictly reduces.

## Implementation Steps

1. In `engineering_facts/writers/json_writer.py`, prune `JSON_ARTIFACTS` to
   `{"config_runtime_map": "config_runtime"}` (single live entry). The
   per-artifact write loop becomes a no-op for the 9 dead files; the
   aggregate `engineering_facts.json` write at :34-35 is untouched.
2. In `harness.py`, shrink `LARGE_CONTEXT_ARTIFACTS` to
   `{"engineering_facts.json", "config_runtime_map.json"}`. The
   `_bounded_context_files` consumer at :3642-3653 is unchanged.
3. In `harness.py:_write_audit_bundle` (`:3976-3999`), replace the 24-line
   body with: mkdir `_audit/`, write `enterprise_gates.json` (from the
   `gate_report` param), write `final_doc_hashes.json` (SHA-256 of
   `staging/*.md`). Drop the 3 file-copy lines (`facts_manifest.json`,
   `documentation_obligations.json`, `provider_calls.json`) and the 3
   dir-copy lines (`_assessment`, `_analysis`, `_investigation`). The
   function signature and call sites (2 — `phase3_docs.py:231` and
   `phase4_cleanup.py:37-39`) are unchanged.
4. In `tests/test_providers/test_engineering_facts/test_builder_writers.py:59-60`,
   drop `entity_index.json` from the asserted set; replace the
   `entity_index.json` read assertion with an assertion that the file does
   NOT exist. Add parallel negative assertions for the other 8 dead maps.
5. In `tests/test_providers/test_engineering_facts/test_service.py:38`,
   drop the `entity_index.json` existence assertion. Add a negative
   assertion for all 9 dead maps.
6. In `tests/test_providers/test_custom_provider.py`, add
   `test_audit_bundle_only_writes_gates_and_hashes`: stage a populated
   staging dir (with a few `.md` files, a `gate_report` dict, plus copies
   of `_assessment/`, `_analysis/`, `_investigation/`, `facts_manifest.json`,
   etc.), call `provider._write_audit_bundle(ctx, state, gate_report)`,
   assert `_audit/` contains exactly `enterprise_gates.json` and
   `final_doc_hashes.json` (use `os.listdir` or equivalent, not just
   `exists()`).
7. In the same test file, add
   `test_load_enterprise_gate_results_falls_back_to_audit`: write only
   `_audit/enterprise_gates.json` (not `_assessment/`), call
   `projects._load_enterprise_gate_results(staging_dir)`, assert the
   parsed dict equals the written content. This pins the FR-7 REST
   invariant.
8. Edit `knowledge_base/CLAUDE.md`: remove the four removed-artifact
   bullets at lines 76-80; rewrite line 81 to say only `status.json` and
   `output.md` are always-on (the other four are debug-gated); add a
   one-line note about `MCP_PIPELINE_DEBUG` next to the staging-dir
   bullet near line 65.
9. Edit `knowledge_base/.agent/system/mcp-workflow.md`: delete the
   "Evidence And Answerability Artifacts" section (lines 29-44); fix
   the Phase 2.8 description (line 18) and input-flow paragraph
   (lines 24-26) to say `_analysis/_doc_pack_*`; delete the
   `AnswerabilityAuditor` paragraph (lines 115-118); add a new short
   section after the Phase Table documenting `MCP_PIPELINE_DEBUG`
   (default `0`; set to `1` to materialize `_debug/` with the full
   forensic record; per-call `manifest.json` is always-on).
10. Acceptance run on the reference project: capture pre-slice baseline
    (staging file count, total bytes, per-call manifest list+size) on
    a clean LobsterProfileExportKoopmansTest run with `MCP_PIPELINE_DEBUG=0`;
    apply Slice 5; re-run; assert: file count ≤ 300, total bytes ≤ 20 MB,
    per-call manifest diff empty. Document results in the PROGRESS_LOG
    entry.

## Verification Strategy

- `pytest` suite green (587 baseline + 2 new + adjusted engineering-facts tests
  should not regress).
- `py_compile` clean on `json_writer.py`, `harness.py`, both test files.
- Grep gate: zero matches for `entity_index.json`, `usage_index.json`,
  `architecture_map.json`, `data_flow_map.json`, `api_surface.json`,
  `known_risks.json`, `test_coverage_map.json`, `change_impact_matrix.json`
  in `engineering_facts/writers/json_writer.py` and `harness.py:_write_audit_bundle`.
  The `_base_files` list in `evidence_bundles.py` still names `engineering_facts.json`,
  `sql_usage.json`, `config_runtime_map.json` (KEEP).
- `validators.py:1559-1564` unchanged (its candidates list is already
  consumer-correct).
- New tests: `test_audit_bundle_only_writes_gates_and_hashes` and
  `test_load_enterprise_gate_results_falls_back_to_audit` both pass.
- Docs: `rg "source_fact_ledger|evidence_retention|answerability_audit|doc_evidence_auditor"`
  in `knowledge_base/CLAUDE.md` and `knowledge_base/.agent/system/mcp-workflow.md`
  returns zero matches. `rg "MCP_PIPELINE_DEBUG"` returns at least one match
  in each file.
- Paired-run acceptance: per-call `manifest.json` context file lists and
  sizes are byte-identical pre- and post-Slice 5 (LLM-input invariance).
  Staging file count drops by ~9+ from removing the 9 dead JSONs; total
  staging bytes drop by ~10 MB; `_audit/` shrinks from a tree of ~20-50 MB
  to two files (<1 MB).
- Edge case: `MCP_PIPELINE_DEBUG=1` — the existing `_debug/` writes continue
  to work; the new `_audit/` only contains the 2 synthesized files.

## Out of Scope

- The Track B work (Phase 3 input quality — per-doc pack filtering,
  budgeted retrieval, evidence-slice promotion). Different track, output-affecting.
- Any LLM-prompt change. Track B.
- The validator parentheses bug, incremental-regen cache bug, preamble
  stripping, scorecard weight-format mismatch (PRD section 6).
- The hash-compute `try/except OSError: pass` silent-swallow in
  `_write_audit_bundle:3996-3997` (Worth flagging in the inspection report
  if still present after Step 3 — the silent-fail becomes more visible once
  `_audit/` shrinks to two files, but FR-7 does not require changing it).
- Resumability of removed files (not applicable — no resumable consumer
  ever read the 9 dead JSONs or the wholesale `_audit/` copies).
- Hardening the REST endpoint's `JSONDecodeError` ordering in
  `_load_enterprise_gate_results` (existing edge case where a corrupt
  `_assessment/enterprise_gates.json` short-circuits the `_audit/` fallback).
  Pre-existing behavior, not FR-7.
