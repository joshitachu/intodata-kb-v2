---
feature: Track A — doc-generation artifact and dead-code reduction
pipeline_step: 2_prd
---

## Executive Summary

Cut the doc-generation pipeline's artifact output from 666 files / 52 MB to ~290
files / ~20 MB per run and delete ~4,400 lines of dead code, while keeping final
docs byte-identical. All reductions are backed by a code-level consumer trace
(`.plans/audit/06_artifact_reduction_findings.md`). Done means: a default run writes
only files with a named consumer, full forensics are available behind one debug
flag, and the codebase/docs no longer describe modules and artifacts that never run.

## Problem

A single doc-generation run writes 666 files / 52 MB to `docs_staging/` for a 10-doc
(~390 KB) deliverable. ~60% of bytes are never read by any phase, validator, gate,
resume path, REST API, or RAG push (verified on the LobsterProfileExportKoopmansTest
run): `_model_inputs/` (9.9 MB), nine root JSON maps (10.4 MB, 100% duplicated inside
`engineering_facts.json`), `_analysis/_bundle_*.md` (1.6 MB behind a fallback that
never fires), 200 of 250 `_agent_tasks/` files, most `_assessment/` reports, and the
`_audit/` wholesale tree copy (~8 MB on enterprise runs). Separately, ~3,800 lines of
audit modules are only called from tests — their harness loaders are guaranteed
no-ops — plus ~600 lines of orphaned duplicate phase bodies in `harness.py`. The
noise makes debugging impractical and the agent docs (`knowledge_base/CLAUDE.md`,
`.agent/system/mcp-workflow.md`) describe artifacts that never exist.

## Goals

1. Default run of the reference project writes ≤ 300 files / ≤ 20 MB to
   `docs_staging/` (baseline: 666 / 52 MB).
2. Final docs byte-identical given identical model responses; bytes reaching every
   LLM call unchanged (verifiable via per-call `manifest.json` diff before/after).
3. Every file written in a default run has a named consumer (phase input,
   validator/gate, resume/rerun, REST API, RAG push).
4. `MCP_PIPELINE_DEBUG=1` restores full forensic output under `_debug/`.
5. Dead modules, dead harness paths, and orphaned phase bodies deleted; test suite
   green; resume, rerun invalidation, enterprise-gate endpoint, dashboard, RAG push
   unaffected.
6. `knowledge_base/CLAUDE.md` and `.agent/system/mcp-workflow.md` updated — no
   references to artifacts/modules that no longer exist.

## Non-Goals

- Changing any LLM input, prompt construction, or pack content (Track B, separate
  feature; findings doc section 7).
- Fixing the four audit bugs: coverage-validator parentheses false-negative,
  incremental-regen `analysis_cache` break, LLM preamble leak into docs 00/07,
  scorecard contract weight-format mismatch (findings doc section 6; separate work).
- Slimming `engineering_facts.json` to usage-only records (optional stretch; only if
  slicing shows it is low-risk — otherwise a follow-up).
- Wiring up the dead audit modules (decision: delete; recoverable via git).
- Renaming/restructuring artifacts that are kept (no consumer churn beyond what
  removals force).

## User Stories

1. As the platform owner debugging a bad doc, I want `docs_staging/` to contain only
   files that something actually consumes, so I can trace "why did the LLM write X
   in doc Y" without wading through 650 irrelevant files.
2. As the platform owner investigating a failed run, I want one env var
   (`MCP_PIPELINE_DEBUG=1`) that turns on complete per-call forensics in a single
   `_debug/` directory, so deep diagnosis is available on demand without polluting
   every run.
3. As a developer maintaining the harness, I want dead modules and duplicate phase
   bodies removed, so the code I read reflects what actually executes.
4. As an operator on a client VPS, I want runs to write ~60% less disk per project,
   so repeated regenerations don't fill the disk with exhaust.
5. As a future agent session reading `.agent/` docs, I want the documented artifact
   list to match reality, so I don't build on top of features that never run.

## Functional Requirements

FR-1 (R1 — debug flag): Add `MCP_PIPELINE_DEBUG` (bool, default false) to
`mcp_server/config.py`. Introduce a single debug-write helper used by all
human-only artifact writes. When false, the following are NOT written; when true,
they are written under `docs_staging/_debug/`:
  a. `_model_inputs/` `prompt.md`, `context_bundle.md`, `request.json`,
     `response.json` (per-call `manifest.json` remains ALWAYS written in its
     current location — it is the primary debugging breadcrumb).
  b. `_agent_tasks/…/` `contract.json`, `validation_report.json`,
     `tool_call_ledger.json`, `conversation_trace.json`, `provider_metrics.json`,
     `attempts/**` (`status.json` and `output.md` remain always-on — resume/rerun
     consumers).
  c. Write-only `_assessment/` reports: `traceability_*.json`,
     `consistency_report.json`, `sql_usage_reconciliation.json`,
     `sql_usage_publication.json`, `_scorecard_contract_errors.json`, `repair/*`,
     `reviewers/*.json`, `warnings.json`, `phase_costs.json`.
  d. `_investigation/_coverage_report.json`; root `_pipeline_log.md`,
     `provider_calls.json`, `index.json`; `_evidence_registry/evidence_registry.md`
     and `survival_report.json`.

FR-2 (R2 — dead JSON maps): Stop writing `entity_index.json`, `usage_index.json`,
`dependency_map.json`, `architecture_map.json`, `data_flow_map.json`,
`api_surface.json`, `known_risks.json`, `test_coverage_map.json`,
`change_impact_matrix.json`. Keep the `.md` and `.csv` variants (scoped-tool
corpus) and keep `engineering_facts.json`, `sql_usage.json`,
`config_runtime_map.json` (live consumers). Update `validators.py:1560-1563`
fallback candidate lists and `evidence_bundles._base_files` in the same change.
Shrink `LARGE_CONTEXT_ARTIFACTS` accordingly.

FR-3 (R3 — dead bundles): Stop writing `_analysis/_bundle_{bundle}.md` and delete
the never-hit doc-pack fallback (`harness.py:5077-5079`). Remove or keep the
defensive delete in `git_reingestion.py:232` — decide in plan.

FR-4 (R4 — dead code): Delete `providers/custom/source_fact_ledger.py`,
`evidence_retention.py`, `answerability_audit.py`,
`services/doc_evidence_auditor.py`, the retention half of `repair_planner.py`
(`retention_repair_plan`, `repair_packet_markdown`), their harness
loaders/gates/no-op repair path (`harness.py:3002-3179`), the Phase 4
answerability summary (`phase4_cleanup.py`), the always-"skipped" answerability
gate entry, and the five corresponding test files. Relocate the
`SourceScanCompleteness` type (currently imported from `source_fact_ledger.py`)
to a live module if any production import remains.

FR-5 (R4b — orphaned phase bodies): Delete `_phase1_investigation`
(`harness.py:1378`), `_phase26_scorecard` (`:2245`), `_phase4_cleanup` (`:4333`)
— zero callers; the `pipeline/phases/*.py` classes are the live implementations.

FR-6 (R5 — transient merge): Consolidate `_context_slices/`,
`_analysis/_merge_inputs/`, `_analysis/_synthesis_inputs/`,
`_analysis/_repair_context/`, `_assessment/_scorecard_reduce/` into one transient
directory deleted at phase end. `_model_slices/` stays (resume cache).

FR-7 (R6 — audit tree): Replace the `_audit/**` wholesale copies of
`_assessment`/`_analysis`/`_investigation`/`provider_calls.json`
(`harness.py:4308-4331`) with `_audit/enterprise_gates.json` +
`_audit/final_doc_hashes.json` only. The REST API fallback
(`api/routes/projects.py:786`) must keep working.

FR-8 (R7 — prompt corpus dedup): After `materialize_prompt_packs` builds
`_assessment/prompt_packs/*.md`, remove the duplicated `_preprocess/` source
copies (`_ref_*`, `_scoring_rubric.md`, etc.) or skip materialization and
reference originals — one copy total. Remove the three-way fallback logic
(`harness.py:4948-4959, 5063-5073, 5089-5099`).

FR-9 (R8 — coverage dup): Write `_source_coverage.json` once
(`_preprocess/_source_coverage.json`); delete the duplicate write at
`source_context.py:334-335`; update both readers' candidate lists
(`validators.py:1591-1592`, `harness.py:2500`).

FR-10 (docs): Update `knowledge_base/CLAUDE.md` (Current Custom Harness Notes —
remove source_fact_ledger/evidence_retention/repair/answerability artifact claims;
document `MCP_PIPELINE_DEBUG`) and `.agent/system/mcp-workflow.md` (delete the
"Evidence And Answerability Artifacts" section; remove the `AnswerabilityAuditor`
paragraph; fix the Phase 2.8 output description — it writes
`_analysis/_doc_pack_*`, not `_preprocess/`; document the debug flag and `_debug/`
layout).

FR-11 (tests): Update `test_custom_provider.py:2885` (asserts `_model_inputs`
existence) for flag-off default; add tests for flag-on `_debug/` output, resume
after reduction (accepted-task skip via `status.json`+`output.md` only), and the
enterprise-gate endpoint with the new `_audit/` shape.

## Non-Functional Requirements

- Default-run artifact budget: ≤ 300 files and ≤ 20 MB on the
  LobsterProfileExportKoopmansTest reference project.
- LLM-input invariance: byte-identical context per call before/after (compare
  per-call `manifest.json` file lists + sizes across paired runs).
- No new runtime dependencies; no API schema changes; no DB/schema changes.
- Resume/rerun behavior unchanged: a run interrupted mid-Phase-2/3 resumes and
  skips accepted tasks exactly as before.
- Disk I/O strictly decreases; no measurable pipeline-runtime regression.
- Debug flag read once via `config.py` — no scattered `os.environ` reads.

## Architecture Impact

- **Harness pipeline (critical area — `/inspect` mandatory)**:
  `providers/custom/harness.py` (artifact writes, debug helper, audit bundle,
  prompt-corpus fallbacks, dead bodies), `pipeline/phases/phase4_cleanup.py`
  (index/answerability/provider_calls), `phase2_analysis.py`/`phase26_scorecard.py`
  (transient dirs, repair-record writes), `agent_runtime.py` (task-dir writes),
  `source_context.py`, `validators.py`, `evidence_bundles.py`, `evidence_agents.py`,
  `engineering_facts/` writers, `config.py`.
- **Module deletions**: `source_fact_ledger.py`, `evidence_retention.py`,
  `answerability_audit.py`, `services/doc_evidence_auditor.py`, retention half of
  `repair_planner.py`.
- **MCP API**: no route changes; consumers preserved — `projects.py:785-786`
  (enterprise gates incl. `_audit/` fallback), `generate_runs.py:295-305` (rerun
  invalidation via `scorecard.md` + `status.json`).
- **No impact**: RAG retrieval chain, auth/RBAC, dashboard frontend (REST only),
  scanner CLI, doc catalog, prompt templates.
- Per `.agent/system/mcp-workflow.md`: phase table stays valid; the "Evidence And
  Answerability Artifacts" section becomes obsolete (FR-10).

## Implementation Phases

**Phase A — dead code removal (FR-4, FR-5).** No artifact-behavior change beyond
artifacts that were never written anyway. Deliverables: module + test deletions,
harness loader/gate removal, orphaned bodies removed. Validation: full pytest,
grep for dangling imports, one end-to-end generation run completes.

**Phase B — debug flag + write gating (FR-1, FR-3, FR-6, FR-8, FR-9).**
Deliverables: `MCP_PIPELINE_DEBUG` in config, debug-write helper, gated writes,
transient-dir consolidation, prompt-corpus dedup, single coverage write.
Validation: paired runs (flag off/on) on reference project — flag-off meets the
file/MB budget, flag-on reproduces full forensics under `_debug/`; per-call
`manifest.json` diff empty; resume test green.

**Phase C — consumer-coupled removals + docs (FR-2, FR-7, FR-10, FR-11).**
Deliverables: JSON-map removal with validator/evidence-bundle updates in the same
commit, `_audit/` manifest replacement, CLAUDE.md/mcp-workflow.md updates, test
updates. Validation: enterprise-mode run — gate endpoint serves from new `_audit/`
shape; scoped-tool evidence search still resolves the `.md`/`.csv` corpus; full
suite green; final-doc byte-diff vs baseline empty.

## Risks and Open Questions

- **Untraced consumer** of a removed file breaks resume, rerun invalidation, the
  dashboard gate view, or a client-side reader of `docs/_facts/`. Mitigation:
  consumer trace is file:line-verified; resume/rerun tests per phase; phased
  delivery isolates blast radius.
- **Compliance (open)**: does any enterprise client contract require full `_audit/`
  tree copies rather than hashes? Code cannot answer this — confirm before/at
  release; FR-7 is trivially revertible to flag-gated full copies if so.
- **Debug-flag trade-off**: failed production runs need a re-run with
  `MCP_PIPELINE_DEBUG=1` for deep forensics. Accepted; per-call `manifest.json`
  stays always-on as the breadcrumb.
- **Resume-slice assumption**: context slices regenerate deterministically before
  the cache check (`harness.py:3528` vs `:3549`) — required by FR-6; verify with a
  mid-phase-interrupt test.
- **Hidden test coupling**: tests beyond `test_custom_provider.py:2885` may assert
  removed artifacts; surfaced mechanically by the suite in Phases A/B.

## Acceptance Criteria

- [ ] Reference-project default run writes ≤ 300 files and ≤ 20 MB to `docs_staging/`.
- [ ] Paired before/after runs: per-call `_model_inputs/**/manifest.json` context
      file lists and sizes are identical (LLM-input invariance).
- [ ] Final docs byte-identical to baseline given identical recorded model responses.
- [ ] `MCP_PIPELINE_DEBUG=1` produces `_debug/` with per-call prompt/context/request/
      response, agent-task traces/contracts/ledgers, and the gated assessment reports.
- [ ] Default run writes none of: `_model_inputs/*/{prompt.md,context_bundle.md,request.json,response.json}`,
      `_agent_tasks` debug files, the nine dead JSON maps, `_bundle_*.md`,
      `evidence_registry.md`, `survival_report.json`, `_pipeline_log.md`,
      `provider_calls.json`, `index.json`, or the write-only `_assessment` reports.
- [ ] `source_fact_ledger.py`, `evidence_retention.py`, `answerability_audit.py`,
      `doc_evidence_auditor.py`, retention half of `repair_planner.py`, and orphaned
      harness phase bodies are deleted; no dangling imports.
- [ ] Interrupted run resumes correctly: accepted tasks skipped via
      `status.json`+`output.md`; rerun invalidation endpoint still works.
- [ ] Enterprise-mode run: gate endpoint returns gates from the new `_audit/` shape
      (`enterprise_gates.json` + `final_doc_hashes.json` only).
- [ ] Scoped-tool evidence search (`search_evidence`/`read_artifact`) still resolves
      the `.md`/`.csv` engineering-facts corpus.
- [ ] Full pytest suite green; `/inspect` verdict PASS or WARN on harness changes.
- [ ] `knowledge_base/CLAUDE.md` and `.agent/system/mcp-workflow.md` contain no
      references to removed artifacts/modules and document `MCP_PIPELINE_DEBUG`.
