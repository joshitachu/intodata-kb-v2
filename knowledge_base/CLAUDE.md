# intodoc -- Code Intelligence Platform

Multi-project, multi-platform tool that scans integration codebases (Talend, MuleSoft, dbt,
etc.), generates comprehensive documentation, and feeds a RAG knowledge base.

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Scanner CLI | `intodoc/` | Parses source code into structured scan cache JSON |
| MCP Server | `mcp-server/` | Exposes scan data to Claude Code via MCP tools + REST API |
| Dashboard | `dashboard/` | React control panel for managing projects, scans, docs |
| RAG Pipeline | `../RAG_pipeline/` | FastAPI + Qdrant for document search |

## Quick Commands

```bash
# MCP server (stdio, for Claude Code)
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
- Phase 2 dispatches one persisted group-analysis task per scanner group, with configurable concurrency (`MCP_AGENT_PHASE2_CONCURRENCY`, default 6). Oversized/split group work is merged by an LLM merge task, not by Python-authored prose.
- Phase 2.6 scorecard and Phase 3 document writing are also persisted agent tasks under `docs_staging/_agent_tasks/{phase}/{target_id}/`.
- Phase 3 dispatches one document task per required markdown document, with configurable concurrency (`MCP_AGENT_PHASE3_CONCURRENCY`, default 4).
- **Phase 2 section parser** (`_parse_phase2_sections`): recognises h3-only headings (`### SECTION_NAME`). All-caps sub-headings (e.g. `#### HIGH` inside `RISK_FLAGS`) do not break the owning section. `CROSS_CHUNK_FINDINGS` is a registered section name. Plugin ANALYSIS_GUIDE h3 sections are registered at call time via `_guide_section_names` + `extra_section_names` param.
- **Phase 2.8 BASELINE floor**: `BASELINE_BUNDLE_SECTIONS = {GROUP_SUMMARY_SECTION}`. Every doc pack includes `GROUP_SUMMARY` plus its own `phase2_sections` from `doc_catalog.yaml` (standard runs) or `evidence_profiles` (plugin/pack runs). Five previously shared sections (`COMPONENTS`, `EXECUTION_FLOW`, `CROSS_GROUP_REFS`, `RISK_FLAGS`, `UNCLASSIFIED`) were removed from the floor.
- **Phase 3 targeted dispatch (non-plugin runs)**: For tool-capable providers (non-plugin mode), `_complete_core_generation` injects the targeted doc pack (`_doc_pack_*.md`, capped at 100 KB) into `opts.system_prompt` instead of sending an empty context (hint-only). Plugin-mode Phase 3 disables tools entirely (`_tool_policy` line 727) so this path is unreachable for plugin runs. Phase 2 calls are unaffected (no `_doc_pack_` files in their context).
- **Phase 3 static context** (`_phase3_context_files`): `_doc_pack_manifest.json` is excluded — it is a build-artifact index, not evidence. `entity_index.csv` and `usage_index.csv` are excluded from `_engineering_fact_context` for all Phase 3 doc names; they remain on disk for tool access.
- `EvidenceTraceabilityValidator` (`providers/custom/evidence_traceability.py`) runs
  after Phase 3 and writes `_assessment/evidence_traceability.json`.
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

### Steven Project Finding

- For project `Steven`, the generated docs/RAG answer incorrectly claimed `Web.config` and `Global.asax.cs` were unavailable. Local source and scan output show both files exist and scanner output contains substantial content.
- The real issue is a documentation synthesis/coverage bug: Phase 3 docs inferred absence from thin normalized artifacts such as `config_runtime_map.md` / `dependency_map.md`, even though `scan_result.json` contains the relevant `content` / `source_code`.
- Do not repeat raw secrets from Steven. `Web.config` contains sensitive app settings and database connection strings. Any fix must preserve credential redaction before LLM calls and should document connection names/systems without exposing secret values.
- Follow-up fix direction: ensure generic-platform evidence tools / EngineeringFacts surface redacted config keys and database connection names from `Web.config`, plus OrmLite registration names from `Global.asax.cs`, so docs stop saying configuration is missing when scanner evidence exists.

### Safe Re-Runs (Staging Directory)

Doc generation uses a **staging directory** (`docs_staging/`) to protect existing docs:

1. Claude Code writes new docs to `output/{project_id}/docs_staging/`
2. On **success**: old `docs/` is removed, `docs_staging/` is renamed to `docs/`, then server pushes to RAG automatically
3. On **failure**: `docs_staging/` is deleted, old `docs/` remains intact, `doc_count` unchanged

Key files: `claude_service.py` (prompt + staging dir setup), `projects.py:_run_generate_background` (swap logic + server-side RAG push).

**Do NOT** add `insert_to_rag` back to the Claude Code prompt -- RAG push is handled server-side in `_run_generate_background` via `RagBridge.push_docs()`.

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
  .claude/                          -- Claude Code settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- This file
  mcp-server/                       -- MCP + REST API server (Python, FastAPI)
  dashboard/                        -- React control panel (Vite + Tailwind, :5173)
  intodoc/                          -- Scanner CLI (see intodoc/CLAUDE.md)
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```
