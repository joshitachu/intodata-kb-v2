---
feature: Remove Phase 3 targeted repair mechanism
pipeline_step: 1_grill
---

## Problem

Phase 3 targeted repair is unreliable, often wrong-triggered, and expensive:

1. **Unreliable**: Today's lobster run had 1 doc (`01_ARCHITECTURE_MAP`) fail the
   "External Systems" obligation check. The repair retried 3 times with
   `failure_kind=unknown` from both primary and fallback models. 30 minutes of
   silent hang with no progress to the dashboard.
2. **Wrong-triggered**: Even when the repair "succeeds", the operator has to
   verify the patched text didn't introduce regressions (the
   `_phase3_targeted_repair_content` helper does a regex-patched merge that
   can stomp on existing content — see `test_phase3_targeted_repair_can_fix_evidence_register_gap`
   which validates the merge is non-destructive, but the real-world assertion
   "did the model introduce a hallucination?" is unverified).
3. **Expensive**: Each repair call is 47K-400K input tokens, 60-300 seconds
   latency. A 3-attempt retry on 1 doc is 3-15 minutes of model spend, on
   top of the original doc generation.

**What "done" looks like:** Live run on Lobsterprofiles_v3 (or any project
where 1+ doc fails an obligation check) reaches Phase 4 within ~5 min after
doc writes complete. Run summary shows the failure as a warning, not a
30-minute retry loop. Final docs are byte-identical to what the model wrote
(no merge, no patch).

**Verification:** Re-run Lobsterprofiles_v3 (resume from the existing staging
preserved by the cancellation). Time the run from doc-write-complete to
Phase 4. Confirm `_run_manifest.json` shows `phase4_cleanup` as the last
phase. Confirm log shows the obligation gap as `warning` not `error` and
no retry attempt is recorded.

## Scope

- **In scope:** Remove the Phase 3 targeted repair mechanism entirely. Keep
  the validators (`validate_docs`, `validate_doc_depth`,
  `validate_source_provenance`) — they still run and still emit warnings. Keep
  the obligation check report writes. Remove the model-call retry path. Run
  completes normally; the warning surfaces in the run summary; the docs ship
  as-written.
- **Out of scope:** Phase 2 repair (`_phase2_targeted_repair` +
  `_narrow_repair_context`) stays. Traceability validator stays. The 4 PRD
  out-of-scope bugs (validator parentheses, incremental-regen cache, preamble
  stripping, scorecard weight-format mismatch) stay for a separate slice.
  `_write_repair_context_summary` and `_repair_root_causes` are removed
  (only Phase 3 callers). The smart-repair config flag
  (`agent_smart_repair_enabled`) becomes Phase 2 only.

## Risk

- **Risk:** Docs that previously got repaired will now ship with missing
  sections. The operator is the new repair mechanism (manual fix in the
  generated docs). If a project regularly produces docs with missing
  obligations, this becomes operational toil.
  **Mitigation:** The warning is now visible in the run summary and the
  staging tree. The operator can see "doc 01 missing External Systems
  section" immediately, not after a 30-min hang. If many projects need
  this, a future slice can add a non-model "structural patch" (e.g., insert
  a placeholder section template without LLM call).

- **Risk:** Track B (Phase 3 input quality) might change which obligations
  fail. Removing the repair before Track B ships could mean the run summary
  is noisier than it needs to be.
  **Mitigation:** Track B reduces the obligation gap by giving Phase 3
  better per-doc context. The warning surface area shrinks. If Track B
  lands before the next lobster run, this concern is moot.

- **Risk:** Phase 4 cleanup or downstream consumers (RAG push, dashboard)
  assume the doc obligation check was the final gate. If they currently
  short-circuit on a failed obligation, removing the repair might surface
  a hidden assumption.
  **Mitigation:** Audit shows Phase 4 only reads `_audit/enterprise_gates.json`
  and the 10 final `.md` docs. It does not re-run `validate_docs`. The
  obligation check is advisory; the swap happens regardless (in
  `_run_generate_background`). The atomic swap is not gated on
  obligation-level warnings.

- **Critical area:** Yes — harness pipeline phases (`/inspect` required).

- **What breaks if wrong:** Phase 3 docs that fail obligation checks will
  ship to RAG. If a chatbot serves a doc with a missing section, the user
  gets a wrong/empty answer. This is the same behavior as today for
  un-repairable cases (the run was hanging instead of producing wrong
  output, which is arguably better, but the data was the same). Net
  behavior change: faster failure visibility, no "stuck" perception, no
  silently-patched hallucination risk.
