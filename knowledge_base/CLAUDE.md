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
8. **No workarounds** -- fixes must address the root cause, not suppress symptoms. If a proper fix requires broader changes, make them. Never ship a narrow patch when the defect pattern exists elsewhere in the codebase.
9. **Generalize fixes** -- before fixing anything, check whether the same failure can occur in other places. Fix it at the level that prevents all recurrences. One-off special-case branches are not acceptable solutions.
10. **Use design patterns** -- before writing a new class, service, or module, identify whether an established pattern fits (Strategy, Repository, Factory, Observer, Pipeline, etc.). Don't invent bespoke structures when a well-understood pattern solves the problem.
11. **Full pipeline gate** -- any non-Scout change requires the full pipeline: `/grill` → `/prd` → `/slices` → `/plan` → `/plan-approve pass` → build → `/inspect` → `/validate` → commit. See root `CLAUDE.md` Development Workflow.
12. **Inspector required** -- after building any change to harness pipeline phases or MCP API routes, run `/inspect` before committing. Max 2 block cycles before escalating to the human.
13. **Emergency path** -- use `/lfe-force` for production emergencies only; it logs to `PROTOCOL_DEBT.md` and session-start will warn until `/debt-resolve` clears it.

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
- Runtime task directories store `status.json` and exact LLM `output.md` as
  always-on breadcrumb artifacts; `contract.json`, `validation_report.json`,
  `tool_call_ledger.json`, and `provider_metrics.json` are written under
  `docs_staging/_agent_tasks/<phase>/<target>/_debug/` only when
  `MCP_PIPELINE_DEBUG=1`. Resume should skip accepted task artifacts (via
  `status.json`+`output.md`) and retry rejected/incomplete tasks independently.
- Python may construct contracts, constrain context/tools, validate coverage, trigger retries/repairs, and persist exact LLM output. Python must not synthesize business explanations or fill final documentation prose itself.
- Generic `list_artifacts` / `read_artifact` tools must not be exposed to Phase 2/3 core generation without hard byte and accumulated input-token caps. Phase 2 core writing uses bounded context files by default; retrieval may be agentic only in budgeted selection/repair steps.
- **Doc catalog**: standard document definitions are in `mcp-server/data/doc_catalog.yaml`,
  loaded by `providers/custom/doc_catalog.py`. Plugin packs can override this with
  their own `doc_catalog.yaml` at the pack root.
- **Prompt templates**: prompt bodies live in `mcp-server/data/prompt_templates/`
  and are rendered via `providers/custom/prompt_renderer.py` (`${SLOT}` substitution).
  Plugin packs can override individual phase prompts by placing `.md` files in
  `{plugin_pack_dir}/prompts/`.
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
