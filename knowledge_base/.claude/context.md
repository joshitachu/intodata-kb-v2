# Current State

**Status**: Template Management & Scoring Pipeline implemented (Phases A-F complete). New Phase 2.6 scoring sub-agent, DB-backed template system with versioning/profiles, full dashboard UI (catalogue, editor, profiles, A/B comparison, document reference). Topology graph endpoint serving live in production. Topology job detail panel now sources job summaries and per-job doc sections from the structured `**Purpose**` lines in `02_ARCHITECTURE_INVENTORY.md` (no LLM calls).
**Last session**: 2026-04-10
**Last completed**:
- Topology perf round 3 (backend half): cleaned up the runtime fallback in `mcp-server/src/mcp_server/api/routes/projects.py` so `/projects/{id}/graph` no longer synthesizes hub-shaped `REFERENCE:*` "external systems" for legacy scans missing `job_external_systems`. Extended `_FALLBACK_INTERNAL_COMPONENTS` (the set checked at the top of `_fallback_talend_system`) to include the tRunJob diagnostic family (`tLogCatcher`, `tStatCatcher`, `tFlowMeter`, `tFlowMeterCatcher`, `tChronometerStart`, `tChronometerStop`, `tAssert`, `tAssertCatcher`); added a defensive `and not system.startswith("REFERENCE")` filter to the Talend branch of `_derive_systems_from_job_json`; capped the per-job result at `sorted(systems)[:6]` so a single hub-job can't fan out across the entire view. Modern scans populate `job_external_systems` and bypass this path entirely, so projects like `esbmain` are unaffected. After this, scherpenhuizen-talend now uses the same code paths as the other Talend projects -- if a project ever needs the noisy hub edges back, regenerate the scan so `job_external_systems` is populated explicitly. **Requires `sudo systemctl restart mcp-server`** for the editable install to pick up. The matching frontend half is logged in `RAG_pipeline/.claude/PROGRESS_LOG.md` (Perf round 3).
- Fixed `/topology` "Not Found" by reinstalling the systemd unit (was running 2+ weeks stale, predating the topology feature) and migrating `intodoc-output/` + `output/` from the old `intodata-kb/` checkout to `intodata-kb-v2/`. Also installed missing `ollama` Python dep.
- Fixed the topology Job Detail panel: added `_extract_job_purpose_from_inventory` and `_extract_job_doc_sections` in `routes/projects.py`; rewrote `get_job_detail()` to use them and dropped the `SummaryService.load_summaries` call. The `**Purpose**:` line from `02_ARCHITECTURE_INVENTORY.md` is now the canonical job summary; "From the Docs" returns only sections whose heading exactly matches the job name (bounded by next sibling heading), instead of dragging in the entire inventory. `summary_service.py`, the `/generate-summaries` route, the `ollama_summary_model` config, and the `ollama` dep all stay in place as dormant code.

## Important: Data Layout

- `intodoc-output/` and `output/` live at `/home/intodata/intodata-kb-v2/knowledge_base/`. The old paths under `/home/intodata/intodata-kb/knowledge_base/` are symlinks back to v2.
- The systemd `mcp-server` service uses `WorkingDirectory=/home/intodata/intodata-kb-v2/knowledge_base` and the venv at `/home/intodata/intodata-kb-v2/venv` (which is itself a symlink to `/home/intodata/intodata-kb/venv` -- the only venv with `mcp_server` editable-installed pointing at v2 source).
- After ANY edit to `deploy/mcp-server.service`, re-copy to `/etc/systemd/system/`, `daemon-reload`, and `restart mcp-server`. Editing the repo file alone does nothing.
- After adding a new dep to `mcp-server/pyproject.toml`, `pip install` it into `/home/intodata/intodata-kb/venv` (the real venv) -- editable installs do not auto-install new deps.

## What's Done

### Scanner CLI (intodoc/)
- Multi-platform architecture: Talend, Workato, Generic implemented
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
