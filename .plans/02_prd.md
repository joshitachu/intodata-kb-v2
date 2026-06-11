---
feature: Remove Phase 3 targeted repair mechanism
pipeline_step: 2_prd
---

## Executive Summary

Phase 3 targeted repair is unreliable, often wrong-triggered, and expensive.
A 30-minute silent hang in the lobster run is the symptom. Remove the
mechanism. Validators stay — they still produce obligation reports and
warnings. Docs ship as-written; the obligation gap surfaces in the run
summary. Done when the live lobster run reaches Phase 4 within ~5 min after
doc writes complete, with the gap logged as `warning` not `error`.

## Problem

Phase 3 currently has a "targeted repair" pass that:

1. Runs after Phase 3 writes all 10 docs
2. Calls `validate_docs`, `validate_doc_depth`, and
   `validate_source_provenance` on the result
3. If any return `ok=False`, retries with a model call that produces a
   patched version of the failing doc (`_phase3_targeted_repair`)
4. Re-validates; if still failing, retries up to 3 times
5. If all retries fail with `failure_kind=unknown`, the run hangs silently

Three problems with this:

- **Unreliable**: 30-min silent hang today, with no progress to the
  dashboard. The 3-attempt loop has no fast-fail on
  `failure_kind=unknown`. Both providers can fail in sequence; the loop
  just keeps trying.
- **Wrong-triggered**: The repair prompt asks the model to fix a doc
  based on a list of error messages. The model often returns text that
  passes the re-validation check (e.g., the Evidence Register merge in
  `_phase3_targeted_repair_content` does a topic-keyed merge that hides
  the original failure) but may have introduced new hallucinations or
  removed content the operator wanted.
- **Expensive**: Each repair call is 47K-400K input tokens, 60-300s
  latency. A 3-attempt retry on 1 doc is 3-15 minutes of model spend on
  top of the original doc generation. For projects where multiple docs
  fail (rare but possible), the cost compounds.

## Goals

1. Live run on Lobsterprofiles_v3 (or any project with failing obligations)
   reaches Phase 4 within ~5 min after doc writes complete.
2. Run summary shows the obligation gap as a warning, not a 30-min retry
   loop.
3. Final docs are byte-identical to what the model wrote (no merge, no
   patch, no LLM-revised text).
4. The validators (`validate_docs`, `validate_doc_depth`,
   `validate_source_provenance`) still run and still produce their reports
   under `_assessment/`.
5. The unit-test suite stays green (590 → 588 baseline; the 2
   repair-specific tests are removed).

## Non-Goals

- Phase 2 repair (`_phase2_targeted_repair`, `_narrow_repair_context`)
  stays. It has the same theoretical issue but lower call volume and
  is out of scope for this slice.
- The 4 PRD out-of-scope bugs (validator parentheses,
  incremental-regen cache, preamble stripping, scorecard weight-format
  mismatch) stay for a separate slice.
- The traceability validator stays (different concern, also advisory).
- The smart-repair config flag (`MCP_AGENT_SMART_REPAIR_ENABLED`)
  becomes Phase-2-only. Default `False` is unchanged.
- Track B (Phase 3 input quality) is unblocked and continues separately.
  Track B reduces the obligation gap by improving Phase 3 context
  assembly — orthogonal to this slice.

## User Stories

1. As the platform owner, I want failed obligation checks to surface as
   warnings in the run summary (visible immediately) rather than trigger
   a 30-minute silent repair loop, so I can decide whether to manually
   fix the doc or accept it as-is.
2. As the platform owner, I want final docs to be byte-identical to what
   the model wrote, so I don't have to audit a regex-patched merge that
   may have introduced a hallucination.
3. As a developer debugging a Phase 3 obligation gap, I want the gap to
   appear in the run log as a `warning` line, so I can grep for it
   without waiting 30 minutes.

## Functional Requirements

**FR-R1 (remove Phase 3 repair orchestration)**: Delete the three
`if not <report>.ok:` repair-then-revalidate blocks in
`phase3_docs.py:129-139, 166-176, 210-223`. Each block currently calls
`self._helpers.phase3_targeted_repair(ctx, state, <report>, log_cb)` and
re-validates. After removal, the revalidation call stays (so the
`_assessment/doc_validation_report.json` etc. files are still written)
but the repair call is gone. The exception-raising `raise RuntimeError` at
line 141 is also removed — failed obligations are warnings, not errors.

**FR-R2 (remove Phase 3 repair implementation)**: Delete the
`_phase3_targeted_repair` method, the `_phase3_repair_targets` helper,
the `_phase3_repair_prompt` helper, the `_phase3_repair_context_files`
helper, and the `_phase3_targeted_repair_content` method. All from
`harness.py`. The `_write_repair_context_summary` and `_repair_root_causes`
helpers stay (Phase 2 still calls them).

**FR-R3 (remove repair facade)**: Delete the
`HarnessHelpersImpl.phase3_targeted_repair` method at
`helpers.py:235-236`. After removal, no other code path can call the
removed method.

**FR-R4 (replace repair tests with warning tests)**: Delete the two
`test_phase3_targeted_repair_*` tests. Add a new test
`test_phase3_obligation_gap_logs_warning` that creates a scenario where
`validate_docs` returns `ok=False` and asserts the obligation gap appears
in the log as a warning line (no model call is made).

**FR-R5 (update CLAUDE.md and mcp-workflow.md)**: Remove the
"Phase 3 targeted repair" mention if present. Add a one-liner: "Failed
obligation checks surface as warnings; the docs ship as-written."

## Non-Functional Requirements

- **No runtime behavior change for successful runs.** A run where all
  obligations pass must produce byte-identical output to today (modulo
  the absence of the repair-call `_model_inputs/phase3.repair/*` files
  and `_agent_tasks/phase3_doc/<doc>/_debug/repair_*` artifacts).
- **Run completes in < 1 min after doc writes** when obligations fail
  (today: 30+ min).
- **No new config flags.** `MCP_AGENT_SMART_REPAIR_ENABLED` becomes
  Phase-2-only; default `False` is unchanged.
- **No DB or API schema changes.**
- **The atomic swap behavior is unchanged.** `_audit/enterprise_gates.json`
  is still written (FR-7 from Track A Slice 5).

## Architecture Impact

- **Harness pipeline (critical area — `/inspect` mandatory)**: `harness.py`
  (5 method deletions), `phase3_docs.py` (3 block simplifications),
  `helpers.py` (1 method deletion).
- **Tests**: `test_custom_provider.py` (2 test deletions, 1 test addition).
- **Docs**: `knowledge_base/CLAUDE.md` (one-line update),
  `knowledge_base/.agent/system/mcp-workflow.md` (one-line update).
- **No impact on**: Phase 1 investigation, Phase 2 analysis / 2.6
  scorecard / 2.8 bundles, evidence quality gate, Phase 4 cleanup,
  MCP API routes, RAG retrieval chain, auth/RBAC, dashboard, scanner,
  doc catalog, prompt templates.

## Implementation

Single slice. The plan steps are:

1. Delete the 3 repair-then-revalidate blocks in `phase3_docs.py`. Each
   block becomes a single validation report write (keep the report) +
   a `log_cb` warning if `not <report>.ok` (new — emit the gap as a
   warning). Remove the `raise RuntimeError`.
2. Delete `_phase3_targeted_repair` and the 4 helpers in `harness.py`.
3. Delete `HarnessHelpersImpl.phase3_targeted_repair` facade.
4. Delete the 2 `test_phase3_targeted_repair_*` tests. Add
   `test_phase3_obligation_gap_logs_warning`.
5. Update `CLAUDE.md` and `mcp-workflow.md` (one line each).
6. Run pytest (expect 588 passing).
7. Re-run Lobsterprofiles_v3 (resume from preserved staging). Time
   from doc-write-complete to Phase 4. Verify `_run_manifest.json`
   shows `phase4_cleanup` as the last phase.

## Risks and Open Questions

- **Risk**: Operator will need to manually fix docs that previously got
  auto-repaired. This becomes operational toil if many projects
  produce failing obligations. **Mitigation**: Track B reduces the
  obligation gap by improving Phase 3 context assembly. If Track B
  lands before the next run, this concern is moot.
- **Risk**: Phase 4 cleanup or downstream consumers (RAG push, dashboard)
  may have hidden assumptions about the obligation check being a
  hard gate. **Mitigation**: Audit confirms Phase 4 only reads
  `_audit/enterprise_gates.json` and the 10 final `.md` docs. The
  swap is not gated on obligation-level warnings. (Verified in
  Track A Slice 5 inspection.)
- **Open question**: Should the warning include the list of
  failing obligations, or just a count? Default: include a count +
  first 3 obligation messages. Operator can re-run validate_docs to
  see the full list.

## Acceptance Criteria

- [ ] `_phase3_targeted_repair` and the 4 helpers are deleted from
      `harness.py`.
- [ ] The 3 repair-then-revalidate blocks in `phase3_docs.py` are
      replaced with single validation writes + warning emissions.
- [ ] The `raise RuntimeError` for failing obligations is removed.
- [ ] `HarnessHelpersImpl.phase3_targeted_repair` is deleted.
- [ ] The 2 `test_phase3_targeted_repair_*` tests are deleted.
- [ ] `test_phase3_obligation_gap_logs_warning` is added and passes.
- [ ] `CLAUDE.md` and `mcp-workflow.md` are updated (1 line each).
- [ ] Full pytest suite green (588 tests).
- [ ] `py_compile` clean on all touched files.
- [ ] No new config flags. `MCP_AGENT_SMART_REPAIR_ENABLED` defaults
      to `False` and becomes Phase-2-only.
- [ ] Live re-run of Lobsterprofiles_v3 reaches Phase 4 within
      ~5 min after doc writes complete.
- [ ] `_run_manifest.json` shows `phase4_cleanup` as the last phase
      for the re-run.
- [ ] `/inspect` verdict PASS or WARN; no BLOCK before committing.
