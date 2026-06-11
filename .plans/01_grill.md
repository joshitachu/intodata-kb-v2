---
feature: Track A — doc-generation artifact and dead-code reduction
pipeline_step: 1_grill
date: 2026-06-10
---

## Feature

Reduce the documentation-generation pipeline's artifact output and supporting code by
~50% while keeping the final docs byte-identical. Implements the R1-R8 plan from
`.plans/audit/06_artifact_reduction_findings.md` (the cross-session findings document
for this work). Strictly reduction: no changes to what any LLM call receives, no bug
fixes (the four bugs found during the audit are logged in section 6 of the findings
doc as separate work).

## Problem Statement

A single doc-generation run writes 666 files / 52 MB to `docs_staging/` while the
deliverable is 10 markdown docs (~390 KB). Verified on the
`LobsterProfileExportKoopmansTest` run:

- ~60% of bytes are never read by any phase, validator, gate, resume path, REST API,
  or RAG push: `_model_inputs/` (9.9 MB), 9 root JSON maps (10.4 MB, 100% duplicated
  inside `engineering_facts.json`), `_analysis/_bundle_*.md` (1.6 MB, differ by one
  header line, dead fallback), 200 of 250 `_agent_tasks/` files, most `_assessment/`
  reports, the `_audit/` wholesale tree copy (~8 MB enterprise runs).
- ~4,400 lines of dead code: `source_fact_ledger.py`, `evidence_retention.py`,
  `answerability_audit.py`, `services/doc_evidence_auditor.py`, the retention half of
  `repair_planner.py` (all only called from tests; their harness loaders are
  guaranteed no-ops), plus three orphaned duplicate phase bodies in `harness.py`.
- The owner cannot tell which artifacts matter when debugging; the artifact surface
  buries the ~15 files with actual debug value. CLAUDE.md describes assessment
  artifacts that never exist in any run.

## Definition of Done

1. A default run of the reference project produces ≤ ~300 files / ≤ ~20 MB in
   `docs_staging/` (from 666 / 52 MB), with final docs byte-identical given identical
   model responses (verified by running with a fixed/recorded provider or by diffing
   the per-call input manifests before/after).
2. `MCP_PIPELINE_DEBUG=1` restores full forensic output under a single `_debug/`
   directory; default runs omit it.
3. Every file still written in a default run has a named consumer (phase input,
   validator/gate, resume/rerun, REST API, or RAG push).
4. Dead modules and orphaned harness phase bodies are deleted; full test suite passes;
   resume, rerun invalidation, enterprise gate REST endpoint, dashboard, and RAG push
   still work.
5. `knowledge_base/CLAUDE.md` and `.agent/` docs updated to stop describing
   artifacts/modules that no longer exist.

## Out of Scope

- Any change to LLM inputs or prompt construction (Track B — Phase 3 input quality —
  is a separate future feature; findings in section 7 of the findings doc).
- The four bugs found during the audit (validator parentheses false-negative,
  incremental regen `analysis_cache` break, LLM preamble leaking into docs 00/07,
  scorecard contract weight-format mismatch). Logged in findings doc section 6 as
  separate work items.
- Slimming `engineering_facts.json` to usage-only records (R2 follow-up, –10 MB in
  shipped `docs/_facts/`) — optional stretch, decide at slicing.
- Wiring up the dead audit modules — decision: DELETE them (recoverable via git).

## Solution Rationale

Every reduction item is backed by a code-level consumer trace (file:line) in
`.plans/audit/06_artifact_reduction_findings.md`, produced by a full audit of the
harness/phases code cross-checked against a real 666-file run. Alternatives
considered: (a) keeping dead modules and wiring them up later — rejected, they are
no-ops misleading both humans and agent docs; (b) post-run cleanup script — rejected,
papers over the root cause (writing exhaust nobody reads); (c) keeping full `_audit/`
tree copies — replaced with `enterprise_gates.json` + `final_doc_hashes.json`
(hash-based tamper evidence), per owner decision.

## Assumptions and Risk

1. **No external consumer of the 9 root JSON maps or `docs/_facts/` JSON** beyond
   what the code trace found. RAG push reads only digit-prefixed `.md`; REST API
   reads only `enterprise_gates.json`, `_agent_tasks/*/status.json`, `_analysis`
   existence, final docs. Could be wrong if a client tool reads `docs/_facts/`
   directly off the VPS.
2. **No compliance contract requires full `_audit/` tree copies.** Owner accepted
   hash-based replacement; flag to clients if an enterprise contract says otherwise.
   (Explicit uncertainty: this is a contractual/compliance question the code cannot
   answer.)
3. **Resume semantics are fully captured** by the traced consumers
   (`_agent_tasks/*/{status.json,output.md}`, `_model_slices/`, `_run_manifest.json`,
   gate/report JSONs listed in findings section 2). A missed resume reader would
   break crash recovery — mitigated by resume-path tests.
4. **Context slices regenerate deterministically on resume** (`harness.py:3528` runs
   before the cache check at `:3549`) — required for R5's transient-dir merge.
5. The one test asserting `_model_inputs` existence
   (`test_custom_provider.py:2885`) is updated alongside R1, not worked around.

## Critical Areas Touched

- **Harness pipeline phases** (`knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/`)
  — YES: phase4_cleanup changes, harness.py artifact writes, dead phase bodies.
  `/inspect` required without exception.
- **MCP API routes** — marginal: no route changes planned, but
  `api/routes/projects.py` / `generate_runs.py` consumers must keep working
  (enterprise gates endpoint, rerun invalidation). Treat as touched; `/inspect`
  covers it.
- RAG retrieval chain — no. Auth/RBAC — no.

## Risk if Wrong

- Deleting a file with an untraced consumer breaks resume (stuck/duplicated runs),
  rerun invalidation, the dashboard's enterprise-gate view, or incremental regen.
- Over-aggressive gating behind `MCP_PIPELINE_DEBUG` makes failed production runs
  undiagnosable until re-run with the flag (accepted trade-off; per-call
  `manifest.json` stays always-on to keep the primary debugging breadcrumb).
- Dropping `_audit/` copies could violate an enterprise client's audit expectations
  (assumption 2) — surface in release notes.
- Mitigations: implement in slices with the resume/rerun test suite green per slice;
  keep R2 validator-fallback updates in the same commit as the JSON removal;
  byte-identical doc verification on the reference project before/after.
