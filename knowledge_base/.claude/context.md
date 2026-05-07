# Current State

**Status**: EP-000 US-101 through US-106 complete (2026-05-05). Per-phase model routing, perf regression tests, doc quality comparison, human review API, and instruction pack loader all done. EP-000 is fully implemented.
**Last session**: 2026-05-05
**Last completed**:
- 10-step doc gen reliability overhaul (2026-04-29): zero-doc=success fix, RAG push retry+propagate, 5 silent failure fixes (chunk defs, template resolution, quick scan, quality scoring, assessment), config centralization (11 new fields), SUPPORTED_PLATFORMS constant, platform validators in schemas+routes, concurrency guard (set-based), Phase 2 resume skip, RAG _read_md_files top-level-only filter, phase3_max_tokens raised to 32000, quality scorer scaled to unit_count, _JOB_RE extended for Workato.
- Doc gen cleanup: deleted `orchestrator.py` + `tools.py` (dead code), removed `_adapt_legacy_text()` stripping from `prompt_packs.py`, slimmed `DocGenContext` (removed 8 cli-only fields), deleted `InvestigatorLoop`, simplified provider selection in `claude_service.py`. 46 provider+service tests pass (2026-04-28).
- Custom investigator loop for `MCP_DOC_GENERATION_PROVIDER=custom`: JSON actions (`read_file`, `search_text`, `list_dir`, `finish`), staging-only tools, validated `_investigation/` artifacts, Phase 2/3 evidence threading, and critic/expander pass (2026-04-25).
- Token explosion + quality fix (5 changes): pre-load context for API Phase 2/3, noise filter in preprocess, scorecard skip on failures, split patterns_user (2026-04-24).
- N-pass section-by-section Phase 2 analysis: `_PHASE2_SECTIONS` (5 sections) + `_run_section_series()` — one LLM call per section, 400-500 lines/group expected (2026-04-24).
- N-pass section-by-section Phase 3 doc writing: `_DOC_SECTIONS` per doc + `_phase3_section_prompt()` (2026-04-24).
- Tailscale Ollama integration: `MCP_OLLAMA_BASE_URL`, `ChatOpenAI` with `/v1` endpoint, per-provider URL routing (2026-04-24).
- Dashboard write model selector: `ProjectSettings.tsx` + localStorage persistence (2026-04-24).
- Fixed `<<<FILE:>>>` delimiter failures for single-file writes (raw response mode) (2026-04-24).
- Fixed Kimi k2.6 400 error (disable thinking via `extra_body`) (2026-04-22).
- Gap 1 — retry logic for Phase 2 group analysis and Phase 3 doc writing (once each) (2026-04-22).
- Gap 2 — `write_model` wiring: Phase 3 uses `MCP_DOC_GENERATION_WRITE_MODEL` if set (2026-04-22).
- Gap 3 — Ollama 1-doc-per-call batching to prevent context overflow (2026-04-22).
- Day 3 — scan completeness warnings across all 4 parsers (Talend / Workato / Lobster / Generic) + job-count delta + dashboard surface. 12 new pytest `tmp_path` tests green. No DB migration. (2026-04-20).
- Day 2 — atomic docs swap + startup recovery + staging GC. 12 unit tests green.
- Day 1 — thumbs feedback persistence.
- RBAC fix for Topology + chat Deep Dive picker (2026-04-20).
- Depth regression fix: 3 changes in `claude_service.py` to remove artificial depth constraints (2026-04-17).

**Next steps** (Generic scanner):
- **PRIORITY**: Run a real scan with `--platform generic` on any zip/repo and trigger doc generation. Verify Phase 2 `_analysis/*.md` has deep narrative content (500+ lines per group, CONNECTIONS / EXTERNAL_SYSTEMS / BUSINESS_RULES populated).
- For C# projects: check `scan_result.json` has `manifest.job_external_systems` with "Application Insights" or similar, and `metadata_connections` from Web.config if present.

**Next steps** (RAG pipeline):
- **PRIORITY — Runtime test Phase B**: Trigger doc generation with `MCP_DOC_GENERATION_MODEL=openai/deepseek-v4-pro`. Watch logs for tool call activity (`list_artifacts`, `read_artifact`). Confirm `context_bytes=0` in Phase 2 logs (no pre-loaded files). Verify `_analysis/{group}.md` written with correct content. Then test with `anthropic/claude-sonnet-4-6`.
- **PRIORITY**: Runtime-test the harness provider end-to-end — confirm `_analysis_prompt_pack.md` contains full unstripped analysis guide content (check for lines that used to be filtered, e.g. "Spawn one sub-agent per group").
- **PRIORITY**: Runtime-test the new custom investigator loop on a real scanned project.
  - Configure `MCP_DOC_GENERATION_PROVIDER=custom`.
  - Configure an API model, e.g. `MCP_DOC_GENERATION_MODEL=moonshot/kimi-k2.6` or another supported `provider/model`.
  - Trigger doc generation for a small project first.
  - Watch logs for `[custom] Investigation complete` and `[custom] Investigation coverage gate passed`.
  - While staging exists, inspect `output/{project_id}/docs_staging/_investigation/`:
    - `{group}.json` and `{group}.md` should exist for each group.
    - Evidence items must include exact canonical unit name, category, source file, line range or chunk ref, snippet, and concrete observation.
  - After success, confirm `provider_calls.json` includes phase `investigation`, final docs mention every canonical unit, and `_investigation/` remains for audit while `_preprocess/` and `_analysis/` are cleaned.
- **Focused automated tests for custom investigator/provider**:
  ```bash
  cd knowledge_base/mcp-server
  PYTHONDONTWRITEBYTECODE=1 pytest tests/test_providers/test_custom_provider.py
  ruff check src/mcp_server/providers/custom tests/test_providers/test_custom_provider.py
  ```
  Expected: provider tests pass (`32 passed`) and ruff passes.
- **Known blocker for full `mcp-server` pytest**: DB/API tests fail before provider code because SQLite cannot render PostgreSQL `JSONB` for `generate_runs.template_snapshot`. Fix the model/test dialect compatibility before treating full-suite failures as related to the investigator work.
- **PRIORITY**: Re-run doc generation for `localESBKIMI` (kimi-k2.6) to verify token fix. Expected: total tokens < 4M (was 22.6M), all 4 groups analyzed, 21 jobs in executive summary, score ≥ 3.5/5.
  - Check logs: no `read_file` calls for preprocess/bundle files in Phase 2 or 3
  - Verify `_analysis/Item.md` and `_analysis/SalesOrder.md` created (were always missing before)
  - Check `run_metrics_*.json`: `input_tokens < 4_000_000`, `doc_quality.overall >= 3.5`
- Restart mcp-server (`python3 -m mcp_server --serve --port 8002`) and re-run doc generation for localESBCHECK with `ollama/qwen2.5-coder:7b` or `ollama/gemma3:12b`.
- Verify: Phase 2 analysis is 20-40KB per group (`wc -l output/localESBCHECK/docs_staging/_analysis/*.md`), overall quality score 2.5-3.5+.
- Consider switching to `gemma3:12b` (general model, better sustained writing than qwen2.5-coder:7b) via Settings write model selector.
- Manual e2e for Day 3: (1) run a real scan on `vaxxinova-talend`, grep `warnings` in `scan_result.json` (expect `[]` on a clean project); (2) drop a malformed `.item` under `process/`, re-scan, check `GET /mcp/status/{project_id}` returns `warnings_count > 0`; (3) delete a handful of jobs, re-scan, confirm `job_delta < 0`; (4) open the dashboard and verify the `! N` chip + signed delta render on the latest Scan History row.
- Day 11 (Gap 17) — plug-in prompt repos, replaces template CMS. Audit the 4 `templates*` tables first (`SELECT COUNT(*)` + list profile names/creators) before deletion.
- Gaps 15 & 16 — business decision on multi-model / doc-gen quality claims (not a code task yet).
- Runtime-verify the earlier RBAC fix: superadmin still sees all scanned projects; a user assigned to only one project sees just that project on Topology + chat Deep Dive; direct `GET /projects/{unassigned_id}/graph` returns 403.
- Re-generate docs for a small project to verify the earlier depth-regression fix.
- Re-scan vaxxinova-talend to verify the earlier tmap_data parser fix produces populated data.

## Important: Data Layout

- `intodoc-output/` and `output/` live at `/home/intodata/intodata-kb-v2/knowledge_base/`. The old paths under `/home/intodata/intodata-kb/knowledge_base/` are symlinks back to v2.
- The systemd `mcp-server` service uses `WorkingDirectory=/home/intodata/intodata-kb-v2/knowledge_base` and the venv at `/home/intodata/intodata-kb-v2/venv` (which is itself a symlink to `/home/intodata/intodata-kb/venv` -- the only venv with `mcp_server` editable-installed pointing at v2 source).
- After ANY edit to `deploy/mcp-server.service`, re-copy to `/etc/systemd/system/`, `daemon-reload`, and `restart mcp-server`. Editing the repo file alone does nothing.
- After adding a new dep to `mcp-server/pyproject.toml`, `pip install` it into `/home/intodata/intodata-kb/venv` (the real venv) -- editable installs do not auto-install new deps.

## What's Done

### Scanner CLI (intodoc/)
- Multi-platform architecture: Talend, Workato, Lobster, Generic implemented
- Platform-agnostic shared prompts (12 in `_shared/prompts/`) + platform overrides
- BaseDistiller with platform-specific subclasses (TalendDistiller, WorkatoDistiller)
- 22 Talend-specific prompt files (p01-p22) + 17 Workato overrides (5 shared fallbacks remain)
- 12 consolidated reference docs (00-11) defining output format/depth
- Universal IR with 99.4% expression parse rate (Phase 2)
- Two-layer credential filter: Layer 1 (raw text regex) + Layer 2 (post-parse structural)
  - Standard: passwords, IPs, FQDNs, URLs, emails, credential files, certificates
  - Strict: + file paths, database/schema names, infrastructure var values
  - Paranoid: redact ALL context variable values
  - Custom rules via `.intodoc-redact.yaml`, audit report in `redaction_report.json`
- Chunker: groups jobs by region, ~30K tokens/chunk

### MCP Server (mcp-server/)
- 8 MCP tools for Claude Code integration (add_repo, fetch_json, trigger_scan, get_scan_status, get_chunks, fetch_chunk, insert_to_rag, list_projects)
- REST API on port 8001 for dashboard (12 endpoints)
- Dual mode: `--mcp` (stdio for Claude Code) or `--serve` (HTTP for dashboard)
- Doc generation via Claude Code subprocess with staging directory
- SSE log streaming for real-time dashboard updates
- JWT cookie validation (shared JWT_SECRET_KEY with RAG backend)
- Hindsight memory tools (recall_process_memory, retain_findings, etc.)
- Reset stuck status endpoint

### Dashboard (dashboard/)
- React + Vite + Tailwind control panel
- Project list with status badges and polling (refetchInterval: 10s)
- Action buttons: Scan, Generate Docs, Push to RAG, Reset
- SSE log panel for real-time operation feedback
- JWT auth: credentials:"include" on fetches, 401 redirect

## To Run

```bash
# MCP server (for Claude Code)
cd knowledge_base/mcp-server && python -m mcp_server --mcp

# REST API (for dashboard)
cd knowledge_base/mcp-server && python -m mcp_server --serve

# Dashboard
cd knowledge_base/dashboard && npm run dev

# Run a scan
cd knowledge_base/intodoc && intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output
```

## Recent Changes

- **EP-001 Deployment Foundation realignment** (2026-05-05): EP-001 is now generic deployment foundation rather than customer-specific rollout. nginx/certbot is canonical; Caddy is legacy/deferred. Added root `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`. `deploy/deploy-client.sh` now emits MCP systemd env vars for generated deployments. EP-001 is not accepted until a clean reference VPS passes runbook + smoke test.
- **Fix template creation 500** (2026-03-30): Fixed `MissingGreenlet` in `_template_to_response()` by using `sa_inspect(t).dict` to check if `versions` relationship is loaded before accessing it.
- **Template Management & Scoring Pipeline** (2026-03-24): Full Transparency Layer feature. Phase 2.6 scoring sub-agent (8-dimension rubric, anti-patterns, red flags, security checklist). DB-backed template system (4 new tables, Alembic migration, TemplateResolver with cascade). Dashboard: template catalogue/editor, version timeline with diff, profile management, A/B run comparison, document catalogue. Profile dropdown on Generate button for template swapping.
- **Gate validation between phases** (2026-03-20): Phase 2.5 validates analysis files (>1KB) before Phase 3. Phase 3.5 validates 9 required docs (>500 bytes) before cleanup. Both gates retry failed agents once (max 1 retry). Prompt-only change.
- **Phase 2 filesystem boundary + scaled target** (2026-03-20): Sub-agents prohibited from reading outside staging dir. Analysis target scales with preprocessed data size (5-40KB, ~50% of input).
- **Consolidated analysis guide** (2026-03-20): Single ANALYSIS_GUIDE.md (12KB) replaces 22 bundled prompts (222KB). Dynamic depth scaling replaces hard-coded line minimums. Phase 3 gets dedicated PHASE_3_GUIDE.md. 94% token reduction for Phase 2.
- **Bundle splitting** (2026-03-19): Prompt bundles split at 200KB, ref doc bundles at 80KB. Now superseded by consolidated guide for platforms that have ANALYSIS_GUIDE.md; legacy splitting still works as fallback.
- **Workato p09+p22 overrides** (2026-03-19): Created Workato-specific p09_business_logic.txt and p22_preventive.txt. Replaced all Talend terms. Added API collection, property list, nested steps, event topic extraction targets. Referenced WAP-003--WAP-010 anti-patterns. 17 Workato overrides total (5 shared fallbacks remain: p04, p10, p11, p14, p15).
- **Fix doc gen prompt delivery** (2026-03-19): Fixed 4 broken path resolutions in doc generation. Analysis prompts now use `intodoc.platforms.__file__` (package path). Reference docs use source-tree-relative `__file__` path. Removed redundant Phase 1 MCP calls from prompt. Fixed subprocess cwd to source tree (has `.mcp.json`). Added warning log for failed prompt bundling.
- **Content-based asset discovery** (2026-03-19): Replaced Workato scanner's 12 hardcoded `rglob("*.suffix.json")` calls with content-based classification. New `_classify_json_content()` reads JSON structure (key signatures) to determine asset type (15+ types). Single-pass `_build_asset_registry()` reuses `walk_repo()` output. Added nested folder format support (recipe.json + steps.json siblings, connection.json in dirs). Added property_list, api_collection types. 126 tests pass (51 new).
- **Workato full prompt parity** (2026-03-18): Created 7 missing Workato-specific analysis prompts (p07_state, p12_data_quality, p16_performance, p17_cross_folder, p18_reusability, p20_lineage_matrix, p21_expression_catalog). Workato now has 22 analysis dimensions matching Talend (15 overrides + 7 shared fallbacks). Zero Talend terminology in any Workato prompt. All prompts use real-world Workato best practices (task consumption, recipe functions, layered error defense, datapill formulas).
- **Workato + Generic plugins** (2026-03-17): Added WorkatoPlugin (14 asset types, recursive block walker, datapill extraction, manifest building), GenericPlugin (universal fallback), BaseDistiller (extracted from TalendDistiller), 12 shared prompts, 8 Workato prompt overrides. Registry + scanner_service updated. 93 new tests, 200 total pass.
- **Assessment page** (2026-03-13): New `AssessmentService` generates self-contained HTML assessment from doc 00 (exec summary) + doc 11 (migration). Jinja2 template with sidebar TOC, responsive CSS, print-friendly. Auto-regenerates after doc generation. Endpoints: `POST /generate-assessment`, `GET /assessment`. Fixed missing `logger` import in projects.py.
- **Doc quality consolidation** (2026-03-12): Consolidated 25 reference docs to 12. Removed Phase 2.5 condensation (agents read full analysis). Rebalanced Phase 3: 3 agents with 4/3/5 doc split (Core/Business+Ops/Patterns+User). Added depth targets (200-800+ lines) and narrative rules. Updated scanner_service.py agent group mappings. Updated mcp-workflow.md.
- **Auto-assign creator on ZIP upload** (2026-03-12): Added `request: Request` param and auto-assign INSERT to `upload_zip` endpoint, matching `add_repo` pattern. Non-admin users now see ZIP-uploaded projects immediately.
- **Phase 1 consolidation** (2026-03-11): Security: fixed git clone command injection (URL validation + `--` separator). UX: Fixed useAuth to surface network errors. Added favicon. Deployment: MCP server Dockerfile now runs as non-root (appuser). Testing: Added Vitest setup + 4 API client tests.
- **Round 2 doc gen optimizations** (2026-03-11): Phase 2.5 condensation, pre-bundled analysis prompts + reference docs, large group splitting, rebalanced Phase 3 (Core/Data+Ops/Config+User). Target: ~29-33 min.
- **Doc gen speed optimization + token fix** (2026-03-11): Fixed token extraction (wrong field names). Added `_preprocess_scan_data()` pre-processing step. Phase 2 reads markdown instead of raw JSON. Phase 3 uses 3 inline-data agents instead of 5 file-reading agents. Added file-reading guidance.
- **Doc gen + deep dive token persistence** (2026-03-11): Added `RagBridge.post_usage()` method. Doc generation calls it after `complete_generate_run`. Deep dive SSE stream intercepts usage events and persists via `post_usage(interface="deep_dive", ...)`.
- **Per-project usage widget + ROADMAP.md** (2026-03-10): Added `GET /projects/{id}/usage` endpoint, usage widget in ProjectSettings, created root ROADMAP.md
- **Unified documentation standards** (2026-03-05): Added root-level logging rules, session start requirements, and populated KB .claude/ files
- **Fix stuck "generating" status** (2026-03-03): Added polling fallback, reset endpoint, Reset button in UI

## Blockers

- None currently.

## Next Steps

- [ ] Start EP-002 analysis after reading the cleaned EP-002 backlog and current auth/security implementation.
- [ ] Validate EP-001 runbook on a clean reference VPS when server access is available.
- [ ] **Test resumable generation**: Kill generation mid-Phase 3, verify `docs_staging/` preserved. Re-trigger, verify RESUME MODE in logs. Test `resume=false` for fresh run. Test stale scan detection.
- [ ] **Re-generate docs for vaxxinova-main**: expect >500KB total (was 406KB), 09_RUNBOOK >600 lines, 11_MIGRATION >700 lines. Verify bundle files in `_analysis/_bundle_*.md` and each <350KB. Verify Phase 3 log shows 4 agents reading bundles.
- [ ] **Re-generate docs for esb-main**: verify quality doesn't regress (should stay >=523KB). Verify 3 agents used (below threshold).
- [ ] Restart MCP server: `sudo systemctl restart mcp-server`
- [ ] Verify enriched prompts: re-generate docs for esbmain, compare depth against previous run (09_RUNBOOK failure scenarios, 05_OPERATIONS_RISK error audit tables, 03_DATA_FLOWS column mappings, 11_MIGRATION scenario comparison)
- [ ] Run Alembic migration: `cd mcp-server && alembic upgrade head`
- [ ] Run seed script: `cd mcp-server && python scripts/seed_templates.py`
- [ ] Restart MCP server: `sudo systemctl restart mcp-server`
- [ ] Test template CRUD via dashboard (create, edit, version, diff)
- [ ] Test profile creation and template swapping via Generate button
- [ ] Test doc generation with scoring Phase 2.6 → verify `_assessment/scorecard.md` output
- [ ] Test A/B comparison: generate with 2 profiles, compare runs in dashboard
- [ ] Test filesystem fallback: generate without any DB templates, verify backward compat
- [ ] Deploy to VPS: `docker compose up -d --build`
- [ ] E2E test: doc generation with consolidated guide → verify 12-doc output + scorecard
- [x] Workato scanner plugin (complete, needs real project data for E2E validation)
- [x] Generic scanner plugin (complete)
- [ ] MuleSoft scanner plugin
- [ ] dbt scanner plugin
- [ ] E2E test: `intodoc scan <dir> --platform workato` with real Workato export
- [ ] E2E test: `intodoc scan <dir> --platform generic` with real project

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
