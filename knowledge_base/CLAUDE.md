# intodoc -- Code Intelligence Platform

Multi-project, multi-platform tool that scans integration codebases (Talend, MuleSoft, dbt,
etc.), generates comprehensive documentation, and feeds a RAG knowledge base.

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Scanner CLI | `intodoc/` | Parses source code into structured scan cache JSON |
| MCP Server | `mcp-server/` | Exposes scan data to the custom doc-generation harness, optional MCP tools, and the dashboard REST API |
| Dashboard | `dashboard/` | React control panel for managing projects, scans, docs |
| RAG Pipeline | `../RAG_pipeline/` | FastAPI + Qdrant for document search |

## Quick Commands

```bash
# MCP server (stdio, optional/debug)
cd mcp-server && python -m mcp_server --mcp

# REST API (port 8001, for dashboard)
cd mcp-server && python -m mcp_server --serve

# Dashboard (port 5173)
cd dashboard && npm run dev

# Run a scan
cd intodoc && intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output

# RAG pipeline (port 8000)
cd ../RAG_pipeline/backend && python -m uvicorn src.api.main:app --port 8000
```

## Rules

1. **Read `.agent/README.md` first** -- it tells you which docs to read for your task
2. **Read `.claude/` files before starting work** -- read `context.md`, `PROJECT_MEMORY.md`, `progress.md`, and `PROGRESS_LOG.md` at session start. Follow the unified logging rules in root `CLAUDE.md` (section "Logging Rules") for all changes -- same format, same discipline as `RAG_pipeline/`.
3. **Credential filter first** -- never send raw credentials to any LLM
4. **ASCII-only Markdown** -- no Unicode box-drawing characters in generated docs
5. **No intermediate files** -- only final `.md` docs in `output/{project_id}/docs/` (generated via `docs_staging/`, see Documentation Generation)
6. **Never hardcode group names** -- use whatever `get_chunks()` returns
7. **Never call `trigger_scan()`** unless cache is explicitly stale or missing
8. **Never resolve git credentials by hand** -- call `provider_for_project()` in `services/git_credentials.py`. Reading `project.github_token_enc` directly skips GitHub App and Azure DevOps connections, whose credential lives off the project row. See `.agent/system/mcp-server.md` "Git connection kinds".
9. **No workarounds** -- fixes must address the root cause, not suppress symptoms. If a proper fix requires broader changes, make them. Never ship a narrow patch when the defect pattern exists elsewhere in the codebase.
10. **Generalize fixes** -- before fixing anything, check whether the same failure can occur in other places. Fix it at the level that prevents all recurrences. One-off special-case branches are not acceptable solutions.
11. **Use design patterns** -- before writing a new class, service, or module, identify whether an established pattern fits (Strategy, Repository, Factory, Observer, Pipeline, etc.). Don't invent bespoke structures when a well-understood pattern solves the problem.
12. **Full pipeline gate** -- any non-Scout change requires the full pipeline: `/grill` → `/prd` → `/slices` → `/plan` → `/plan-approve pass` → build → `/inspect` → `/validate` → commit. See root `CLAUDE.md` Development Workflow.
13. **Inspector required** -- after building any change to harness pipeline phases or MCP API routes, run `/inspect` before committing. Max 2 block cycles before escalating to the human.
14. **Emergency path** -- use `/lfe-force` for production emergencies only; it logs to `PROTOCOL_DEBT.md` and session-start will warn until `/debt-resolve` clears it.

## Documentation Generation

The MCP-based 7-phase documentation workflow is documented in:
**`.agent/system/mcp-workflow.md`**

This is the primary method for generating project documentation. Follow it exactly.

### Current Custom Harness Notes

- Doc generation runs a **7-phase pipeline** orchestrated by `PipelineRunner` in
  `mcp-server/src/mcp_server/providers/custom/pipeline/runner.py`.
- Phase bodies live in `providers/custom/pipeline/phases/` (one file per phase);
  `evidence_quality_gate.py` is at the package root (not under `phases/`).
  `harness.py` owns setup, state management, enterprise gate evaluation, and the
  staging swap; it no longer contains phase logic.
- The seven phases are: `phase1_investigation`, `phase2_analysis`, `phase26_scorecard`,
  `phase28_bundles`, `evidence_quality_gate`, `phase3_docs`, `phase4_cleanup`.
- `evidence_quality_gate` is a pure Python phase (no LLM calls) that checks
  `_preprocess/{group}.md` file sizes before Phase 3. Groups below 500 bytes emit a
  `THIN_BUNDLE` warning and are listed in `state.thin_bundle_groups`. Phase 3 prepends
  an evidence notice to all generated docs when thin groups are detected.
- Phase 2 plans job-boundary evidence tasks per group (`providers/custom/evidence_tasks.py`): whole `## Job:` sections are bin-packed up to `MCP_PHASE2_TASK_EVIDENCE_BYTES` (default 70000); a job is never split, and each task's evidence header lists exactly the units it contains. One analysis agent runs per task (`MCP_AGENT_PHASE2_CONCURRENCY`, default 6). Multi-task groups are assembled deterministically in Python (verbatim section selection with per-task provenance) and a single group synthesis task writes `GROUP_SUMMARY` + `CROSS_JOB_FINDINGS`. The former char-based chunker, its lying 'visible in this chunk' headers, and the LLM merge task (`MCP_PHASE2_MERGE_MODE`) were removed — they caused chunk agents to fabricate analyses for jobs whose evidence they never had.
- Phase 2.6 scorecard and Phase 3 document writing are also persisted agent tasks under `docs_staging/_agent_tasks/{phase}/{target_id}/`.
- Phase 3 dispatches one document task per required markdown document, with configurable concurrency (`MCP_AGENT_PHASE3_CONCURRENCY`, default 4).
- **Phase 2 section parser** (`_parse_phase2_sections`): recognises h3-only headings (`### SECTION_NAME`). All-caps sub-headings (e.g. `#### HIGH` inside `RISK_FLAGS`) do not break the owning section. `CROSS_JOB_FINDINGS` is a registered section name (legacy `CROSS_CHUNK_FINDINGS` stays registered so cached analyses parse). Plugin ANALYSIS_GUIDE h3 sections are registered at call time via `_guide_section_names` + `extra_section_names` param.
- **Phase 2.8 BASELINE floor**: `BASELINE_BUNDLE_SECTIONS = {GROUP_SUMMARY_SECTION}`. Every doc pack includes `GROUP_SUMMARY` plus its own `phase2_sections` from `doc_catalog.yaml` (standard runs) or `evidence_profiles` (plugin/pack runs). Five previously shared sections (`COMPONENTS`, `EXECUTION_FLOW`, `CROSS_GROUP_REFS`, `RISK_FLAGS`, `UNCLASSIFIED`) were removed from the floor.
- **Phase 3 targeted dispatch (non-plugin runs)**: For tool-capable providers (non-plugin mode), `_complete_core_generation` injects the targeted doc pack (`_doc_pack_*.md`, capped at 100 KB) into `opts.system_prompt` instead of sending an empty context (hint-only). Plugin-mode Phase 3 disables tools entirely (`_tool_policy` line 727) so this path is unreachable for plugin runs. Phase 2 calls are unaffected (no `_doc_pack_` files in their context).
- **Phase 3 static context** (`_phase3_context_files`): `_doc_pack_manifest.json` is excluded — it is a build-artifact index, not evidence. `entity_index.csv` and `usage_index.csv` are excluded from `_engineering_fact_context` for all Phase 3 doc names; they remain on disk for tool access.
- **Phase 3 evidence lane** (`providers/custom/target_evidence.py`): unit-scoped docs (per-job expansion via `_per_job_doc_units`) get a deterministic `_analysis/_evidence_{doc}.md` bundle built straight from the intodoc scan cache — component inventory with high-signal params, ALL code bodies / tMap expressions / SQL verbatim (top-level `code` preferred, `params[].CODE` fallback), context variables per environment with provenance tags (`job-embedded` vs `shared group '{name}'` vs Default) and DIVERGES flags, callers/callees. The bundle is FIRST in the doc writer's context; the Phase 2 doc pack is interpretation, not the fact source. Size knob `MCP_PHASE3_TARGET_EVIDENCE_BYTES` (default 120000): derived niceties (wiring, param detail) trim first, verbatim evidence is never silently cut. For these docs, large derived fact maps (`dependency_map.md` etc.) are delivered as unit-filtered copies (boundary-aware so `X_GetLocation` never matches `X_GetLocations`), capped by `MCP_PHASE3_FILTERED_MAP_BYTES` (default 24000). Group-level docs keep the previous flow.
- **Fabrication validators** (`providers/custom/fabrication_validators.py`), run pre-acceptance in the agent-task validation ladder (errors land in `validation_gaps` and drive the repair attempt; publish gates stay advisory): (1) component-echo — `tXxx_N` references in output must exist in the evidence given to that agent (target bundle for Phase 3, task evidence file + investigation context for Phase 2 tasks); (2) no-unavailability — claims that code/tMap/SQL/context evidence is "unavailable"/"not captured"/etc. fail validation when the bundle demonstrably contains that category (runtime facts like throughput stay legitimately unavailable). Both were built from verified run-50 failures (invented `tSetGlobalVar_3`; "code unavailable" with the code in context).
- **Job-scoped component-echo for bin-packed Phase 2 tasks** (`job_scoped_component_reference_errors`, wired via `_merge_component_ref_errors(..., job_refs=...)`): verified failure (reversEngineer_ALI run 51) — a bin-packed task covering several sibling jobs in one call can misattribute a component real for one job to a different job in the same batch (`tMSSqlOutput_1`, real only for `AssetCheck_GetLocation`, attributed to sibling `AssetCheck_GetWorkOrder`, which actually uses `tDBOutput_1`); checking the whole task output against the *combined* evidence of every job in the batch lets that pass, since the name is real somewhere in the batch. For any task with `len(task.units) > 1`, `evidence_tasks.job_section_texts()` recovers each unit's own `## Job:` evidence text from the task file, and output is split on the model's own `#### <job>` sub-headings (verified reliable in production without a prompt change) into per-job-attributed spans, each checked against only that job's own evidence; content under a heading that names no known unit (cross-job table/business-rule summaries, e.g. `#### Table: LogInfo` listing which jobs write to it) is unattributed and falls back to the combined task-wide check, so genuine cross-job synthesis is not wrongly rejected — that specific fallback case still relies on the task-wide check alone and is a known open gap, not a false-negative in this mechanism. Single-job tasks are unaffected (no job-scoping overhead). The no-unavailability check is Phase-3-only; it was never wired into Phase 2, so it has no equivalent scoping bug to fix there.
- `EvidenceTraceabilityValidator` (`providers/custom/evidence_traceability.py`) runs
  after Phase 3 and writes `_assessment/evidence_traceability.json`.
- **`_rag_support/` package (layered RAG Slice 2)**: Phase 4 calls
  `services/rag_support_package.py::build_rag_support_package()` before deleting
  `_analysis/` — copies only facts-manifest group analyses into
  `docs_staging/_rag_support/phase2_analysis/` with a `manifest.json`
  (allowlist; never `_doc_pack_*`/`_bundle_*`/reports). The atomic swap carries
  it into `docs/`. Post-swap, `_run_generate_background` restores the flat
  `analysis_cache/` via `replace_analysis_cache_from_rag_support()`; an absent
  or empty package removes stale cache so incremental webhook regen falls back
  to full regeneration. `RagBridge` pushes only top-level `docs/*.md`, so
  `_rag_support/` stays invisible until Slice 3 ingests it deliberately.
- **All 4 Phase 3 final-check gates are advisory warnings, not exceptions.**
  Validators (`validate_docs`, `validate_doc_depth`,
  `validate_source_provenance`) and the SQL usage gate
  (`_validate_sql_usage_publication`) still run and write their
  reports under `_assessment/`. If any return errors, the failure is
  emitted as a `log_cb` warning (count + first 3 messages) and the
  docs ship as-written. No `RuntimeError`. The operator decides
  whether to manually fix the gap. Phase 4 cleanup and the atomic
  swap to `docs/` proceed normally. The 4 gates are:
  doc-validation, doc-depth, source-provenance, and SQL usage. The
  enterprise publish gates (`valid_fresh_scan`,
  `complete_evidence_coverage`, etc.) are also advisory: failed
  gates surface as warnings, the swap happens. (Added 2026-06-11 —
  removed the `_phase3_targeted_repair` mechanism; softened the
  remaining hard-fail gates in a follow-up commit.)
- **Work-plan call/output budgets (P0.1, 2026-08)**: the run call budget is computed at
  run start from the ACTUAL bin-packed work plan (`harness._plan_call_budget`, using
  `evidence_tasks.count_evidence_tasks`) — base + phase1 tasks + phase2 claim tasks +
  scorecard/doc slice allowances — not the old `20 + groups*4` heuristic (which
  under-counted job-dense groups ~7x; verified werkorder budget_exhaustion). The output
  budget is sized per doc-task via per-doc-type ceilings (`_doc_output_ceiling`:
  unit docs 6K, briefs 14K, inventory scales with units). `docgen_max_run_calls` (250)
  stays a hard ceiling; the partition planner (`estimate_partition_calls`) sizes/splits
  partitions with the same units-scaled estimate, and `top_level_partitions()` folds
  size-splits back to zip level for the cross-project index.
- **True-ceiling budget + late-phase floors (R2.1, 2026-08)**: the plan sum is a GUIDE,
  never the enforcement ceiling — the enforced cap is `docgen_max_run_calls` (verified
  Assetcheck failure: run blocked Phase 3 at plan=179 while cap=250 had ~71 calls
  unused; real consumption ~197). `_reserve_model_budget(phase=...)` classifies calls
  (`_budget_class`: phase3 / business_topology / shared) and blocks a call only when it
  would exceed ceiling minus the still-unused floors reserved for OTHER classes
  (`state.planned_phase_floors`: phase3 = required_docs x allowed_slices, topology = 2),
  so early-phase overruns can never starve doc writing; `_phase3_docs` releases its
  floor when dispatch finishes. Plan estimates now include evidence-agent calls, a
  Phase-2 repair-retry allowance (`MCP_DOCGEN_PHASE2_RETRY_ALLOWANCE`, default 0.25),
  ~2 calls/group for agentic Phase 1, and a realistic scorecard slice count
  (1 reducer + ceil(0.75 x groups)) instead of a flat 4.
- **Salvage-first publishing (P0.2, 2026-08)**: per-group Phase 2 failures become PARTIAL
  stubs (`_contain_phase2_group_failures`; group joins `thin_bundle_groups` +
  `partial_groups`, stub intentionally fails `group_analysis_is_complete` so resume
  retries); a failed scorecard writes an explicit "SCORECARD UNAVAILABLE" file and
  continues; a failed/rejected Phase 3 doc becomes a stub (or a truncate-flag-ACCEPTED
  doc on an output-cap hit) recorded in `_assessment/failed_docs.json`, and the run
  publishes everything that passed. The old fail-closed `raise` chain
  (`raise group_errors[0]`, "Doc generation failed for ...", "Scorecard generation
  failed") is gone — accepted content ships with flagged gaps; resume regenerates the
  stragglers.
- **Phase 3 slice governance + merge (P0.3/P2.7, 2026-08)**: `docgen_allowed_slices_per_doc`
  is hard-enforced for doc-level phases (excess evidence slices are dropped with a
  SLICE CAP NOTICE + `_assessment/slice_cap_flags.json`; the call is accepted). Each
  input slice restarts the document, so Phase 3 multi-slice output is merged by section
  (`doc_slice_merge.merge_sliced_doc_outputs`: one title, one TOC, each H2 once,
  duplicate blocks dropped); `duplicate_heading_errors` is the post-assembly guard and
  `dedupe_document` the deterministic last-resort repair in `_final_doc_content`
  (verified fix for the VQ docs that shipped the same structure 11x). Slice prompts are
  byte-stable; the per-slice instruction is a TAIL context file (provider prefix cache).
- **Phase 3 input-partitioned slicing (R2.5, 2026-08)**: for `phase3*` calls whose
  context exceeds the hard ceiling, `phase3_slices.plan_phase3_slices` (wired in
  `_complete_core_generation` ahead of the generic splitter) partitions the INPUT like
  Phase 2 bin-packs evidence: files <= `MCP_PHASE3_SHARED_CONTEXT_FILE_BYTES` (16K) are
  SHARED and byte-stable in every slice; larger files split into whole markdown
  sections (H2, then H3, then line-boundary) distributed so each slice gets ONLY its
  portion; the doc pack's leading small H2 sections (project scope, unit index,
  obligations digest) become a shared `__skeleton.md`. Total input is ~1x the pack +
  N x the small skeleton, not N x everything, and slice count may exceed
  `docgen_allowed_slices_per_doc` when needed for full coverage (partitioned plans are
  exempt from the cap-drop; the verified truncation driver was dropped evidence
  chunks). The generic splitter + cap remain for non-phase3 phases and as fallback.
- **Per-unit reference docs (P1.4/P1.6, 2026-08)**: standard runs expand the
  `10_UNIT_REFERENCE` catalog entry (`per_unit: true`; gate:
  `MCP_DOCGEN_UNIT_DOCS_ENABLED`, default OFF) into one ~80-line dossier
  per scanned unit (template `phase3_unit_doc.md`: protected-facts table, verbatim
  code/tMap/SQL, per-env context with provenance, evidence discrepancies, runtime
  unknowns). Unit docs are written FIRST; group/summary docs then receive a bounded
  deterministic digest of the finished unit docs (`_analysis/_unit_docs_digest.md`,
  `MCP_PHASE3_UNIT_DIGEST_BYTES`) so summaries synthesize from unit docs, not the
  analysis layer alone. Protected facts are a deterministic ledger in the target
  evidence bundle; `protected_fact_errors` / `unit_count_claim_errors`
  (fabrication_validators) reject recomputed facts pre-acceptance.
- **Phase-2 claims density floor (P3.10, relaxed by R2.4, 2026-08)**: the prompt still
  REQUIRES verbatim backticked citations for code-bearing sections
  (TRANSFORMATION_DETAIL/SOURCE_SQL/CUSTOM_CODE), but validation no longer fails the
  whole bin-packed batch for one uncited claim (verified: 21/73 Assetcheck tasks — 29%
  — were rejected whole, stubbing every unit in the task to unknown and producing
  phantom-absence warnings). `validate_claim_batch` fails only STRUCTURAL errors (bad
  JSON, section/unit outside contract, duplicate ids, claim ceilings); evidential gaps
  are `warnings` and `apply_claim_floors` (called post-parse in
  `_run_phase2_claim_spec`, fresh + resume) deterministically DEMOTES the individual
  claim to unknown when it has neither evidence_refs nor a verbatim citation (a claim
  citing evidence_refs is kept even when its statement paraphrases). Component-echo
  fabrication checks still fail the batch. Ceilings unchanged.
- **LLM scorecard + default evidence agents are opt-in (R2.2/R2.3, 2026-08)**: standard
  runs skip the advisory Phase 2.6 scorecard (`MCP_DOCGEN_LLM_SCORECARD_ENABLED`,
  default off; verified: planned 4 calls, used 13 + ~0.8M tokens with contract gaps) —
  `_write_omitted_scorecard_marker` writes a deterministic `_assessment/scorecard.md`
  so the 07 doc / exec summary / enterprise doc-set validator degrade gracefully — and
  run no built-in evidence agents (`MCP_DOCGEN_DEFAULT_EVIDENCE_AGENTS_ENABLED`,
  default off; verified: 3 of 4 defaults produced 0 claims, ~0.6M tokens). Plugin
  packs that declare a scorecard phase or evidence_agents are unaffected.
- **Cache audit (R2.6, 2026-08)**: provider-reported cache hits are logged per call
  (`_log_call_metrics`: `in=/cache_read=/cache_miss=`) and aggregated in
  `_assessment/phase_costs.json`. Phase-2 repair retries keep the prompt + evidence
  packet byte-stable and deliver validation-gap feedback as a transient TAIL file
  (`_transient/claim_repair/`), same pattern as slice instructions — mutating the
  prompt head invalidated the provider prefix cache for the whole packet (verified:
  phase2.claims cache-hit ratio 0.7% vs 66% for the prefix-stable scorecard slices).
- **Runtime evidence (TMC) is a staging artifact, not provider state** (2026-08-24).
  `providers/custom/runtime_evidence.py` fetches bounded, redacted aggregates from the RAG
  backend's runtime mirror (`GET /api/v1/runtime/doc-evidence`) and renders them
  deterministically -- no LLM, no tools. `harness._ensure_runtime_evidence`, exposed on the
  shared facade as `HarnessHelpersImpl.ensure_runtime_evidence`, is the ONE producer: the
  custom pipeline calls it from `Phase3Docs.run`, OpenCode from `_prepare_run`. It writes
  three artifacts and every consumer reads them by name -- the cache
  (`_transient/runtime/runtime_evidence.json`, destroyed by Phase 4),
  `_analysis/_runtime_profile.md` (the project rollup), and
  `_assessment/runtime_evidence_coverage.json` (survives the swap; read by
  `GET /mcp/generate-runs/{run_id}/connector-evidence`). Runtime evidence was previously
  an in-memory attribute populated inside one engine's phase driver, which is exactly why
  OpenCode -- which reuses the CONSUMER (`_phase3_context_files`) but could not reuse the
  producer -- silently shipped zero runtime facts. Producer writes files; consumers read files.
  * **Two identifier keys, always.** `FactsManifest.unit_names()` returns `job_id`
    (`validators.py:184`), NOT the display name TMC schedules under; 79 of werkorder's 429
    job rows differ. The fetch sends `unit.name` AND `unit.display_name` and keys `by_unit`
    on the manifest name. Verified live: manifest-key-only matches 4 units, both keys match
    6. Do NOT add a normaliser or fuzzy matcher -- `AssetCheck_GetLocation` and
    `AssetCheck_GetLocations` are both real, separately-scheduled jobs.
  * **Rollup goes to group/summary docs ONLY** (after `_cross_cutting_facts.md`, plus an
    explicit attach for `09_EXECUTIVE_SUMMARY`, which early-returns before that append).
    Unit docs keep their own per-unit section. A project-wide failure census names other
    units' components, and Phase 3 component-echo validates a unit doc against its own
    bundle -- attaching it there manufactures fabrication rejections, and costs ~1.3M extra
    input tokens on werkorder with unit docs enabled.
  * `phase3_runtime_profile_bytes` (12,000) is CLAMPED at config load to
    `phase3_shared_context_file_bytes - 1`: `plan_phase3_slices` classifies context files
    as SHARED purely by size, so an oversized rollup would be bin-packed and slice 1 would
    see failing jobs that slice 3 does not. `_runtime_profile` also has an explicit rank-0
    branch in `phase3_slices._trim_priority` (its filename must never contain `_evidence_`,
    which auto-promotes).
  * **Absence is always stated.** The rollup is written unconditionally in one of three
    modes (`data`/`opted_out`/`unavailable`) with independent `partial` and
    `scope_mismatch` flags, and the coverage artifact is written even when the fetch fails.
    Low coverage is CORRECT, not a defect: TMC schedules tasks, so werkorder's 6 of 429 is
    right -- the rest are `tRunJob` children and joblets. Never accuse a project of a bad
    binding from match ratio alone; report the runtime-side orphan ratio as an observation.
  * The once-per-run guard is keyed on `(run_id, staging_dir)` **in memory**. An on-disk
    check would make a resumed run adopt a failed run's snapshot and pin `unavailable`.
  * Cadence is classified in Python from server-supplied gap quantiles (per environment,
    named by `environments[0]`), never from `runs_per_day`: the mean gap for
    `Maximo2RHM_Workorders` is 2202 s against a true median of 1801 s, so a mean-fed
    classifier downgrades a 30-minute schedule to "ad hoc".
  * KNOWN LIMITS: the living-docs patch lane (`services/true_patch_executor.py`) never
    reads `_phase3_context_files`, so patched documents gain no runtime prose; the
    tool-enabled Phase 3 first pass discards context files; scoped runs record a
    project-wide coverage denominator with a `run_scope` block noting the discrepancy.
- **Runtime READ scope is per project, and separate from ingest scope** (2026-08-25).
  Ingest scope (`environment_ids`, set in the connect dialog) decides what the
  scheduler POLLS. Read scope (`doc_environment_ids`, `doc_window_days`) decides what a
  documentation run READS out of what was polled -- it costs nothing at the vendor API
  and needs no re-mirroring. Both live in the source's non-secret config (the credential
  blob, this framework's per-source config store); `providers/base.py::DOC_READ_SCOPE_FIELDS`
  appends the read-scope keys to `editable_config_fields` for EVERY provider, and
  deliberately keeps them out of `credential_schema()` so the connect form does not ask
  for them while an operator is holding an API token.
  * **The filter is applied server-side, once**, in `GET /runtime/doc-evidence` via
    `RuntimeStoreRepository.resolve_doc_read_scope`. Both engines inherit it because both
    call the same producer; nothing in the harness passes an environment.
  * **`runtime_executions` carries no environment column; `runtime_job_runs` does.**
    `_environment_execution_ids` joins them on `execution_id` -- safe because the TMC
    provider DERIVES each execution from a job run (`_execution_from_job_run`, copying
    `run_id` into `execution_id`), so the two tables are one-to-one. Every
    execution-based aggregate (reliability, streak, durations) inherits the filter from
    `_scoped_execution_base`; failures join through the same id; environments and gap
    quantiles filter `runtime_job_runs` directly. Filtering per-aggregate instead would
    let one number describe PROD while its neighbour described PROD+TST.
  * **Why it matters, measured on the live werkorder mirror**: unscoped, the estate
    reports 3392 runs / 785 failures = 23.1% failure rate. That figure describes no
    environment that exists -- PROD alone is 2122 runs / 17 failures (0.8%), ACC is 1266
    / 768 (60.7%). Same defect class as the pooled-cadence bug that
    `_dominant_environment_gaps` already guards against.
  * **A run must state its scope.** `RuntimeDocEvidenceResponse.read_scope` is always
    populated (even unconfigured), carried on `RuntimeEvidenceStore.read_environments`,
    and published in the rollup's coverage table plus
    `_assessment/runtime_evidence_coverage.json`. Without it two runs of one project are
    not comparable and a narrowed read is indistinguishable from a shrinking estate.
  * **The picker offers mirrored environments only** (`list_mirrored_environments`, the
    environment inventory UNIONed with observed job runs, backing
    `GET /runtime/doc-read-scope`). Offering the provider's live list would let an
    operator select an environment that was never ingested -- empty evidence, no visible
    cause. A selection that later goes stale is reported as
    `unavailable_environment_ids`, never silently dropped.
  * `environment_ids: None` ("every mirrored environment") is a DISTINCT state from a
    selection listing all of them: a later ingest-scope widening extends the former and
    not the latter. An explicitly empty list returns no evidence rather than everything.
  * NOT DONE: connection and binding are still coupled -- a runtime source carries one
    `project_id`, so one Talend tenant serving N projects means N stored credentials and
    N polls. `runtime_workspace_map` (source -> many projects) exists and is RBAC'd but
    is not wired into the connect flow.
- **Composite child runs** record `triggered_by="composite"` +
  `GenerateRun.partition_name` (new column; idempotent startup migration in
  `api/app.py::_apply_column_migrations`) instead of overflowing `triggered_by` with
  `composite:{name}`.
- Runtime task directories store `status.json` and exact LLM `output.md` as
  always-on breadcrumb artifacts; `contract.json`, `validation_report.json`,
  `tool_call_ledger.json`, and `provider_metrics.json` are written under
  `docs_staging/_agent_tasks/<phase>/<target>/_debug/` only when
  `MCP_PIPELINE_DEBUG=1`. Resume should skip accepted task artifacts (via
  `status.json`+`output.md`) and retry rejected/incomplete tasks independently.
- Python may construct contracts, constrain context/tools, validate coverage, trigger retries/repairs, and persist exact LLM output. Python must not synthesize business explanations or fill final documentation prose itself.
- Generic `list_artifacts` / `read_artifact` tools must not be exposed to Phase 2/3 core generation without hard byte and accumulated input-token caps. Phase 2 core writing uses bounded context files by default; retrieval may be agentic only in budgeted selection/repair steps.
- **Doc catalog**: standard document definitions are in `mcp-server/data/doc_catalog.yaml`,
  loaded by `providers/custom/doc_catalog.py`. Packs replace the document set via the
  `docs:` block in `plugin.yaml` (there is NO pack-root `doc_catalog.yaml` override path).
  The starter every pack is copied from is `mcp-server/data/swap_pack_starter/` --
  fully commented, and asserted to compile by the pack test suite.
- **Packs (2026-08, consolidated)**: there is exactly ONE pack format and it is
  `plugin.yaml` (`services/plugin_schema.py`). A pack is a directory tree --
  manifest, `prompts/`, `prompts/docs/`, `examples/` -- and it lives in one of two
  lanes, distinguished only by `plugin_packs.owner_user_id`:
  **personal** (authored in Nexus, stored under `data/plugin_packs/users/{uid}/`,
  visible and usable only to its owner, git-initialised so publishing is a push) and
  **shared** (a branch of `MCP_SWAP_PACK_STARTER_REPO_URL`, cloned into
  `data/plugin_packs/`, available to everyone). `POST /mcp/plugins/{id}/publish`
  pushes a personal pack as `pack/{name}` and opens a pull request -- it ASKS;
  a human merge is what grants everyone access. `services/pack_architect.py` +
  `/mcp/plugins/architect/sessions/*` are the interactive author: each user turn
  edits one owner-private Pack workspace and deterministic validation gates save.
  The single UI is `RAG_pipeline/frontend/components/packs/PackWorkbench.tsx`
  (proxied via `app/api/packs/*`); the dashboard has no pack surface.
  RETIRED: `pack_schema_v2.py` / `schema_version: 2` / layer+governance+capabilities,
  the `data/packs/` + `MCP_DEFAULT_PACK_ID` activation lane, `/mcp/packs/preview`,
  the RAG DB pack registry (`/api/v1/packs`), and ZIP pack upload.
- **What a pack changes**: extraction is platform-owned and identical for every pack
  (a pack cannot weaken the evidence floor). Three blocks change behaviour --
  `phases` (whether each phase runs, with which model/concurrency), `lens`
  (`focus` = which evidence categories are screened, `sections` = the declared
  analysis contract + the question each section must answer, `requirements` =
  standing prompt rules), and `docs` (the output set + `phase2_sections` routing).
  `lens.sections.mode: replace` is the important lever: the Phase 2 prompt asks for
  ONLY the declared sections, so a focused pack stops paying for built-in questions
  it never wanted. `extend` keeps the old additive behaviour. A pack with no `lens`
  behaves exactly as packs did before the block existed.
- **Pack contract**: every run resolves a `PackContract`
  (`providers/custom/pack_contract.py`) -- `default_contract(platform)` when no pack
  is selected, else `contract_from_plugin_manifest(manifest, platform)` (the ONE
  compiler). The contract owns the Phase 2 section universe, doc routing, focus
  categories, requirements and audience; validators, prompts, claim planning, doc
  bundles, the quality scorer and the living-docs patch lane all read it.
  `GROUP_SUMMARY` is the protected floor no manifest can remove or declare.
- **No loud fails (2026-08)**: an unsatisfied declared section does NOT fail
  anything. A security pack pointed at code with no dynamic SQL *should* come back
  empty for that section -- that is a correct result, not a defect. Such gaps are
  recorded in `_assessment/pack_section_coverage.json` (pack, group, sections, the
  questions that went unanswered) and the documents publish clean: no in-document
  notice, no `completed_warnings` status, no quality penalty, no PARTIAL stub.
  `PackContract.hard_required_names` is the split that guarantees it -- the whole
  required set for a no-pack run (built-in Talend strictness, unchanged) and the
  `GROUP_SUMMARY` floor alone for any pack. REMOVED with this:
  `declared_required_names`, `sections[].required`, `required_platforms`,
  `pack_section_failures.json`, the "Pack Section Contract Violation" notice,
  `WarningCode.PACK_SECTION_CONTRACT`, and the -2.0 scorer penalty.
- **Pack audience -> Phase 3 presentation**: the manifest `audience:` block compiles
  onto `PackContract.audience` (`PackAudienceSpec`) and is injected at the Phase 3
  rendering boundary only. `tone` / `technical_depth` / `output_organization` are
  typed with the `Nexus*` enums (a closed whitelist, validated at load); `roles` is
  bounded free text (max 6 x 80 chars) rendered by
  `readership_instructions_for_phase`, never coerced into a presentation enum.
  A request-supplied `ctx.nexus_presentation` WINS over the pack's audience -- the
  pack supplies the default. Both `doc_prompt` injection sites (enterprise doc +
  per-unit dossier) pass `contract=` so the paths cannot diverge silently.
  (tests: `tests/test_providers/test_pack_contract.py`,
  `test_pack_audience.py`, `tests/test_api/test_personal_packs.py`)
- **Prompt templates**: prompt bodies live in `mcp-server/data/prompt_templates/`
  and are rendered via `providers/custom/prompt_renderer.py` (`${SLOT}` substitution).
  EVERY prompt the pipeline sends is a template file -- Phase 1's three
  investigator prompts (`phase1_extraction.md`, `phase1_chunk_selection.md`,
  `phase1_manifest_extraction.md`), Phase 2's claim extraction
  (`phase2_claims.md`) and section repair (`phase2_section_repair.md`), and
  Phase 3.7's topology synthesis (`phase37_business_topology.md`) were inline
  f-strings until 2026-08 and are now templates. Extraction was verified
  byte-identical to the f-strings they replaced.
- **Two pack prompt namespaces** (do not confuse them):
  * `{pack}/prompts/<slot>.md` -- PREAMBLES substituted into a `${PREAMBLE}`
    slot: `phase2.md`, `phase3.md`, `scorecard.md` (`prompts._resolve_preamble`),
    plus `investigation.md`, which is PREPENDED to Phase 1 rather than
    substituted, and `prompts/docs/<DOC>.md` / `prompts/phase2_platform_sections/
    <platform>.md`.
  * `{pack}/prompts/templates/<template_name>.md` -- the whole prompt BODY,
    named after the template it replaces, resolved generically by
    `PromptRenderer.render(..., pack_dir=)`. A body override wins outright.
    Separate directory because `scorecard.md` exists as both a preamble slot and
    a template, and one namespace would make it silently shadow the other.
  Before the body lane existed ~59% of the run's authored prompt text was
  pack-expressible (preambles + platform skeletons + per-doc instructions);
  the ~41% remainder was the template bodies and the Phase 1 / Phase 2-claims /
  Phase 3.7 f-strings. It is now ~93%. What is STILL not reachable: the three
  patch templates, `documentation_obligations()` (per-platform Python dicts
  injected into every Phase 3 prompt), and the blocks `group_analysis_prompt`
  assembles in Python -- notably the 521-byte output-size contract, which is
  injected via `${TASK_SCOPE_NOTE}` and so survives a `phase2_analysis.md`
  override. The enumeration obligation and the redaction/fabrication rules DO
  live in the templates and are now overridable.
- **Authoring surfaces read the overridable surface from one place**:
  `services/pack_prompt_slots.py` (`PHASE_TEMPLATES` + `prompt_slots()`),
  derived from the live catalog / platform strategies / template directory.
  `scripts/generate_reference_pack.py` imports `PHASE_TEMPLATES` rather than
  keeping a copy, and `GET /mcp/plugins/prompt-slots` (+ `/{path}` for one
  body) serves it to the workbench. The workbench's *Add prompt override*
  seeds a new file with the live built-in text -- a body override REPLACES the
  built-in, so starting from an empty box silently drops the redaction and
  enumeration rules. Tests: `tests/test_services/test_pack_prompt_slots.py`.
- **A drafted document needs its own prompt.** `_doc_specific_instructions`
  resolves a document name against `doc_catalog.yaml`; a pack's invented name is
  not there, so a manifest declaring `docs: [MY_REPORT]` with no
  `prompts/docs/MY_REPORT.md` renders Phase 3 with an EMPTY `${DOC_SPECIFIC}`
  slot -- the document is still written, from the generic template alone.
  `pack_architect.run_turn` therefore asks the dedicated Pack model for one
  compact, complete design and renders the canonical manifest, prompt files,
  README, and example deterministically. It applies that artifact atomically
  and reports exact `pack_validation` completeness errors after every turn.
  Pack authoring does not use OpenCode or expose tools. Tests:
  `tests/test_services/test_pack_architect.py`.
- **Reachability is the entry criterion for a pack prompt file.** A pack file
  that no `render(..., pack_dir=)` call site reads is dead weight that reads as
  authoritative. `phase2_patch.md` / `phase3_patch.md` /
  `phase3_synthesis_patch.md` are rendered ONLY by
  `services/true_patch_executor.py`, which builds its own `PromptRenderer` with
  no pack context, so overriding them is not possible -- threading the project's
  selected pack into the living-docs lane is open work.
  `tests/test_services/test_reference_pack.py::
  test_every_shipped_template_override_is_reachable_from_the_pipeline` is the
  guard (it greps the source for pack-aware render calls).
- **Talend ESB detection (`providers/custom/talend_intelligence.py`)**: Talend serializes
  DI Jobs and ESB/Camel Routes into the same `.item` format; the only signal is c-prefixed
  Camel component usage. Classification is now **scanner-computed** -- the intodoc Talend
  parser (`intodoc/src/intodoc/platforms/talend/parser.py`, `_ESB_COMPONENT_PATTERN` +
  `classify_esb_components`) sets `is_esb_route` / `esb_component_types` on each `ParsedJob`
  by matching the `c[A-Z]` component-name prefix convention (not a hardcoded name list, so
  unlisted Camel component types are still classified correctly),
  and these flow through `scan_result.json`. **Job discovery** (`_parse_all_jobs`,
  `_find_talend_root`) walks both `process/` (DI Job Designs) and a separate top-level
  `routes/` directory (ESB/Camel Route Designs) -- real Talend Studio exports put Routes
  in `routes/`, not under `process/`; before this, Route items in that layout were never
  parsed at all (not merely misclassified). `talend_intelligence.py::detect_talend_esb_patterns`
  only **consumes** those fields (no duplicated component set); `write_talend_intelligence`
  writes `_preprocess/talend_esb_facts.json` when any route is detected. Guidance is wired
  into Phase 2 via a conditional `ESB_ROUTES` section (`ESB_ROUTES_SECTION` in `constants.py`,
  registered in `CONDITIONAL_SECTION_NAMES`, with a conditionally-worded body in
  `data/prompt_templates/phase2_platform_sections/talend.md` covering routing rules, endpoints,
  EIP patterns, and error/exception channels). `validators.py::validate_analysis_guide_sections`
  enforces the single canonical `ESB_ROUTES` heading (via the shared `_has_section` check) only
  when `talend_esb_facts.json` reports `esb_detected` -- pure-DI projects never hard-fail on it.

### Composite-Upload Orchestration (partition runs)

- **Partition planner** (`providers/custom/partition_planner.py`): a composite-ZIP scan
  (source_roots carrying `.__intodoc_zip__`) partitions into one sub-project per zip;
  an oversized partition (base + groups*per_group > `MCP_DOCGEN_MAX_RUN_CALLS`) is further
  split along whole groups (`split_groups`/`parent` on `Partition`). Single-root small
  projects always yield ONE `__all__` partition — the legacy single-run path, unchanged.
- **Orchestrator** (`services/partition_orchestrator.py`, hooked at the top of
  `projects.py::_run_generate_background`, gated to full runs only — no `scoped_docs`,
  no reingestion `pre_swap_gate` — and by `MCP_DOCGEN_PARTITION_ORCHESTRATION`): loops one
  scoped `DocGenerationCoordinator.generate_docs(partition=...)` per partition into
  `output/{project_id}/{subproject}/` (own docs_staging, generation lock, call budget,
  atomic swap, child GenerateRun `composite:{name}`, RAG push with `{subproject}/`-prefixed
  filenames). Failure isolation: a failed sub-project preserves its staging for resume and
  never rolls back siblings; `topology/partition_state.json` (scan-fingerprint keyed) skips
  up-to-date partitions on resume. Internal RAG layers sync once (single wholesale delete,
  then per-sub prefixed pushes) — never call `sync_internal_layers` per partition.
- **Scoping seam**: `DocGenContext.partition` filters the facts manifest (harness), and a
  per-partition **unit allowlist** (canonical job ids — unique per zip even for shared
  REFERENCES display names) threads through `scanner_service.get_chunk_definitions` /
  `read_chunk_data`, `_preprocess_scan_data`, `EngineeringFactsService.extract_and_write`,
  `write_talend_intelligence`, and `build_business_topology_evidence` so scoped runs never
  see sibling sub-projects' units.
- **Cross-project layer (deterministic, no LLM)**: before any partition run the orchestrator
  writes `output/{pid}/topology/cross_project_index.json` (job-call edges across sub-projects
  with same-partition-first resolution, landscape application edges, shared systems) +
  `CROSS_PROJECT_TOPOLOGY.md` (pushed to RAG at upload level). Each partition run stages
  `cross_project_refs.md` (engineering-fact context for Phase 2/3 — external references
  without sibling content) and `_topology/cross_project_index.json` (consumed by the
  business-topology starter pack).
- **Case-colliding groups**: Talend groups differing only in case (werkorder:
  `VISE2AssetCheck` vs `Vise2AssetCheck`) clobbered each other's `_preprocess/{group}.md`
  on case-insensitive filesystems (macOS). `_preprocess_scan_data` now writes later
  colliders as `{group}_part_ci{k}.md`; the existing `_part*` slice→group resolvers map
  them back.

### Steven Project Finding

- For project `Steven`, the generated docs/RAG answer incorrectly claimed `Web.config` and `Global.asax.cs` were unavailable. Local source and scan output show both files exist and scanner output contains substantial content.
- The real issue is a documentation synthesis/coverage bug: Phase 3 docs inferred absence from thin normalized artifacts such as `config_runtime_map.md` / `dependency_map.md`, even though `scan_result.json` contains the relevant `content` / `source_code`.
- Do not repeat raw secrets from Steven. `Web.config` contains sensitive app settings and database connection strings. Any fix must preserve credential redaction before LLM calls and should document connection names/systems without exposing secret values.
- Follow-up fix direction: ensure generic-platform evidence tools / EngineeringFacts surface redacted config keys and database connection names from `Web.config`, plus OrmLite registration names from `Global.asax.cs`, so docs stop saying configuration is missing when scanner evidence exists.

### Safe Re-Runs (Staging Directory)

Doc generation uses a **staging directory** (`docs_staging/`) to protect existing docs:

1. The custom harness writes new docs to `output/{project_id}/docs_staging/`
2. On **success**: old `docs/` is removed, `docs_staging/` is renamed to `docs/`, then server pushes to RAG automatically
3. On **failure**: `docs_staging/` is deleted, old `docs/` remains intact, `doc_count` unchanged

Key files: `claude_service.py` / `DocGenerationCoordinator` (generation orchestration and provider selection), `providers/custom/harness.py` (custom harness setup/state), and `projects.py:_run_generate_background` (swap logic + server-side RAG push).

**Do NOT** add `insert_to_rag` back to doc-generation prompts -- RAG push is handled server-side in `_run_generate_background` via `RagBridge.push_docs()`.

### Debug Forensics (`MCP_PIPELINE_DEBUG`)

Set `MCP_PIPELINE_DEBUG=1` to materialize the full forensic record under `docs_staging/_debug/` (per-call prompt/context/request/response snapshots, agent-task contracts/ledgers/traces, and the gated `_assessment/` advisory reports). Default runs (`MCP_PIPELINE_DEBUG=0`) keep only the always-on breadcrumb artifacts: per-call `_model_inputs/<phase>/<NNN>_<target>/manifest.json` and `_agent_tasks/<phase>/<target>/{status.json,output.md}`. Use the flag to reproduce a failed run with full traces; do not enable it for production runs (output bloat).

### OpenCode Runtime Homes and Failure Diagnostics

- **A failed OpenCode step now leaves evidence.** `_audit/opencode_errors.json`
  records phase, step, model route, returncode, the *raw* error events (up to 20)
  and stderr (up to 8 KB); the short reason is appended to the raised exception
  and the run log. Before this, `_parse_json_event` reduced an error event to a
  bare type, stderr was counted then discarded, and `opencode_events.json` was
  written only on success -- a failure produced the useless line "OpenCode
  emitted an error event (details redacted)". Keep the split: the full event goes
  to the artifact, only a bounded excerpt (`_MAX_LOG_REASON_CHARS`) reaches the
  log, because a provider message can quote part of the request.
- **Every route gets private XDG homes.** `_phase_config` sets `XDG_DATA_HOME`
  and `XDG_STATE_HOME` to per-invocation 0700 dirs under the same temp root as
  the generated config (removed by `OpenCodePhaseConfig.cleanup`). Previously
  only `opencode-go/` redirected them -- it needed a private `auth.json` -- so
  every other route used the service account's ambient home and inherited
  whatever uid last wrote there. Verified failure: root-owned
  `/app/.home/.local/share/opencode/log` made every `appuser` run die with
  `PermissionDenied: FileSystem.open (.../opencode.log)` before reaching the
  model. `XDG_CACHE_HOME` stays shared at `{data_dir}/opencode_cache` (it holds
  `models.json` and the runtime binary, worth reusing) and is created by the
  service process, so it is always owned by the service account;
  `_preflight` write-probes it and fails with the path rather than letting
  OpenCode die mid-stream.
- **`docker compose exec` runs as root.** Anything that invokes OpenCode inside
  the container must use `-u appuser`, or it leaves root-owned state behind.
  The XDG isolation above makes a run survive that, but the ambient home is
  still shared with `opencode auth`/CLI use.

## Code Conventions

- **Python**: 3.11+, Pydantic v2, Click CLI, hatchling build system
- **Frontend**: React 19, Vite 6, TypeScript 5, Tailwind CSS 3
- **Tests**: pytest (Python), Vitest (frontend)

## Known Pitfalls

- **Windows encoding**: cp1252 breaks Unicode chars -- use ASCII in all output
- **Token bloat**: tMap distiller must strip noise BEFORE chunking
- **ENDPOINT preservation**: don't strip URL/REST params -- they are semantic
- **Connection matching**: use `REPOSITORY_PROPERTY_TYPE` refs, not name matching
- **hatchling**: use hatchling, NOT setuptools -- pyproject.toml is configured for it
- **Budget**: legacy CLI analyzer default $5 limit; 18 passes on 9 chunks can exceed

## Deployment Foundation Notes (EP-001)

- EP-001 has been realigned to generic deployment foundation, not a customer-specific rollout.
- nginx/certbot is canonical for new deployments. Caddy is legacy/deferred.
- Dashboard depends on MCP REST API, and MCP API runs as the `mcp-server` service in the Docker Compose stack.
- Runbook and smoke-test docs are in root `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`.
- Before marking EP-001 done, validate on a clean reference VPS: seven Docker healthchecks, MCP `/health`, HTTPS/security headers, login/logout, ingest, streaming query, dashboard, and IP allowlist.

## Project Structure

```
knowledge_base/                     -- Platform root
  .agent/                           -- Agent context docs (README, system, SOPs, tasks)
  .claude/                          -- legacy agent settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- This file
  mcp-server/                       -- MCP + REST API server (Python, FastAPI)
  dashboard/                        -- React control panel (Vite + Tailwind, :5173)
  intodoc/                          -- Scanner CLI (see intodoc/CLAUDE.md)
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```
