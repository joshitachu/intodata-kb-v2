---
feature: Track A — doc-generation artifact and dead-code reduction
pipeline_step: 3_slices
total_slices: 5
current_slice: 5  # Slices 1-3 approved (5e16911); Slice 4 built+inspected (WARN); planning Slice 5
---

## Slice 1: Dead code deletion (FR-4, FR-5)
**Delivers**: The provider package contains only code that executes in production —
deletes `source_fact_ledger.py`, `evidence_retention.py`, `answerability_audit.py`,
`services/doc_evidence_auditor.py`, the retention half of `repair_planner.py`, their
no-op harness loaders/gates/repair path (`harness.py:3002-3179`), the Phase 4
answerability summary, and the three orphaned duplicate phase bodies in `harness.py`
(~4,400 lines + 5 test files). Pipeline behavior unchanged (all deleted paths were
no-ops).
**Expected files**:
- DELETE `mcp-server/src/mcp_server/providers/custom/{source_fact_ledger,evidence_retention,answerability_audit}.py`
- DELETE `mcp-server/src/mcp_server/services/doc_evidence_auditor.py`
- DELETE `mcp-server/tests/test_providers/{test_source_fact_ledger,test_evidence_retention,test_answerability_audit,test_repair_planner(retention parts)}.py`, `tests/test_services/test_doc_evidence_auditor.py`
- EDIT `providers/custom/repair_planner.py` (remove retention half; keep phase2 plan + patchers + token estimate)
- EDIT `providers/custom/harness.py` (loaders/gates :3002-3179; orphaned bodies :1378, :2245, :4333)
- EDIT `providers/custom/pipeline/phases/phase4_cleanup.py` (answerability summary)
- EDIT any module importing `SourceScanCompleteness` (relocate type if a live import remains)
**Critical area**: yes — harness pipeline phases (`/inspect` required)
**Depends on**: none

## Slice 2: Debug flag + per-call/task forensics gating (FR-1a, FR-1b)
**Delivers**: `MCP_PIPELINE_DEBUG` config flag and a single debug-write helper;
default runs stop writing `_model_inputs/` prompt/context/request/response snapshots
and `_agent_tasks/` contracts/ledgers/traces/metrics/attempts (the two biggest
exhaust sources, ~11 MB / ~365 files); flag-on writes them under `_debug/`. Per-call
`manifest.json` and `_agent_tasks/*/{status.json,output.md}` stay always-on
(debugging breadcrumb + resume/rerun consumers).
**Expected files**:
- EDIT `mcp-server/src/mcp_server/config.py` (`MCP_PIPELINE_DEBUG`)
- NEW debug-write helper (location per plan; e.g. `providers/custom/debug_artifacts.py`)
- EDIT `providers/custom/harness.py` (`_record_model_inputs` writes :3865-3965)
- EDIT `providers/custom/agent_runtime.py` (task-dir writes :353-412)
- EDIT `mcp-server/tests/test_providers/test_custom_provider.py` (:2885 default-off; new flag-on `_debug/` test; resume test via status+output only)
**Critical area**: yes — harness pipeline phases (`/inspect` required)
**Depends on**: none (parallel-safe with Slice 1, but ship after it to avoid harness.py churn)

## Slice 3: Gate remaining write-only artifacts + small write fixes (FR-1c, FR-1d, FR-3, FR-9)
**Delivers**: Default runs stop writing the write-only `_assessment/` reports,
`_investigation/_coverage_report.json`, `_pipeline_log.md`, `provider_calls.json`,
`index.json`, `evidence_registry.md`, `survival_report.json`, and the dead
`_analysis/_bundle_*.md` files; `_source_coverage.json` written once. All routed
through the Slice 2 helper into `_debug/` when the flag is on.
**Expected files**:
- EDIT `providers/custom/harness.py` (assessment report writes, `_PipelineLog` :410-450, bundle writes :2429-2442, dead fallback :5077-5079, reviewers/reconciliation writes)
- EDIT `providers/custom/pipeline/phases/{phase2_analysis,phase26_scorecard,phase3_docs,phase4_cleanup}.py` (repair records, contract errors, traceability writes, warnings/index/provider_calls)
- EDIT `providers/custom/evidence_agents.py` (registry.md :520, survival_report :522)
- EDIT `providers/custom/source_context.py` (:334-335 single coverage write)
- EDIT `providers/custom/validators.py` (:1591-1592 coverage reader candidates), `harness.py:2500`
- EDIT `services/git_reingestion.py` (:232 defensive bundle delete — decide in plan)
- EDIT tests asserting gated artifacts
**Critical area**: yes — harness pipeline phases (`/inspect` required)
**Depends on**: Slice 2 (uses the debug-write helper)

## Slice 4: Transient-dir consolidation + prompt corpus dedup (FR-6, FR-8)
**Delivers**: One transient directory (deleted at phase end) replaces
`_context_slices/`, `_analysis/{_merge_inputs,_synthesis_inputs,_repair_context}/`,
`_assessment/_scorecard_reduce/`; prompt corpus exists once (pack materialization
sources deduplicated, three-way fallback logic removed). `_model_slices/` untouched
(resume cache).
**Expected files**:
- EDIT `providers/custom/harness.py` (slice/merge/synthesis/reduce write paths :4060-4076, :1682, :1753, :3388, :2363; fallback removal :4948-4959, :5063-5073, :5089-5099)
- EDIT `providers/custom/prompt_packs.py` (source-copy cleanup after materialization)
- EDIT affected tests; new mid-phase-interrupt resume test (slice regeneration assumption)
**Critical area**: yes — harness pipeline phases (`/inspect` required)
**Depends on**: Slice 3

## Slice 5: Dead JSON maps + audit-tree manifest + docs + acceptance run (FR-2, FR-7, FR-10, FR-11 remainder)
**Delivers**: The nine never-read root JSON maps are no longer written (validator
fallbacks and evidence-bundle base files updated in the same commit); `_audit/`
reduced to `enterprise_gates.json` + `final_doc_hashes.json` with the REST fallback
intact; CLAUDE.md and mcp-workflow.md match reality; paired-run acceptance executed
(≤300 files / ≤20 MB, LLM-input invariance, byte-identical docs, flag-on forensics).
**Expected files**:
- EDIT `providers/custom/engineering_facts/json_writer.py` (drop the nine aggregates)
- EDIT `providers/custom/validators.py` (:1560-1563 fallbacks), `providers/custom/evidence_bundles.py` (`_base_files`), `harness.py` (`LARGE_CONTEXT_ARTIFACTS` :113-125)
- EDIT `providers/custom/harness.py` (audit bundle :4308-4331 → gates + hashes)
- EDIT `mcp-server/tests/` (enterprise-gate endpoint with new `_audit/` shape; scoped-tool corpus resolution)
- EDIT `knowledge_base/CLAUDE.md`, `knowledge_base/.agent/system/mcp-workflow.md`
**Critical area**: yes — harness pipeline phases + MCP API consumers (`/inspect` required)
**Depends on**: Slices 1-4 (acceptance run measures the cumulative result)
