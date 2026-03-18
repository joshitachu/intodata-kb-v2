# Progress Log

Session log for the intodoc platform. Every session should add an entry documenting
what was done, what changed, and lessons learned.

## Session: 2026-03-18

### [Feature]: Create 7 missing Workato-specific analysis prompts (p07, p12, p16, p17, p18, p20, p21)

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/prompts/p07_state.txt` -- NEW (168 lines, 9.4K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p12_data_quality.txt` -- NEW (199 lines, 11.9K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p16_performance.txt` -- NEW (236 lines, 12.3K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p17_cross_folder.txt` -- NEW (233 lines, 13.3K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p18_reusability.txt` -- NEW (311 lines, 16.8K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p20_lineage_matrix.txt` -- NEW (199 lines, 9.6K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p21_expression_catalog.txt` -- NEW (222 lines, 11.0K chars)
- **Problem**: 7 analysis dimensions that Talend covers (state, data quality, performance, cross-region, reusability, lineage, expressions) were completely missing for Workato -- no platform override AND no shared fallback. These dimensions were skipped during Workato doc generation, producing incomplete documentation.
- **Root cause**: When Workato prompts were initially created, only 8 of 22 dimensions were written as Workato-specific overrides. The remaining 14 were expected to use shared fallbacks, but shared fallbacks only existed for 7 of the 14. The 7 without either platform-specific or shared prompts were silently skipped.
- **Change**: Created 7 Workato-specific prompt files following the established style convention (opening line, Workato Context, What to Extract, Output Format with JSON examples). Each prompt uses exclusively Workato terminology (recipe, datapill, monitor/catch, call_recipe, block tree, provider) with zero Talend terms. Incorporated real-world Workato best practices: task consumption model (batch = 1 task for N records), polling interval optimization, recipe functions vs callable recipes, layered error defense, idempotency patterns, Ruby method chains on datapills.
- **Why this approach**: Creating platform-specific prompts rather than shared fallbacks ensures Workato doc generation uses terminology, concepts, and JSON output examples appropriate to the Workato platform. Shared fallbacks are too generic for these dimensions.
- **Verification**:
  - `get_analysis_prompts('workato')` returns 23 prompts (15 overrides + 7 shared fallbacks + best_practices)
  - `grep` for Talend terms across all 7 new files: zero matches
  - All files follow Workato prompt style (verified against p01_inventory.txt reference)
  - Note: MCP stdio tool call fails due to `.mcp.json` setting `MCP_PROJECT_ROOT=/opt/intodata-kb/knowledge_base` (deployment dir without source code). The systemd REST server (which handles doc generation) resolves correctly.

**Previous Workato prompts (8)**: p01, p02, p03, p05, p06, p08, p13, p19
**New Workato prompts (7)**: p07, p12, p16, p17, p18, p20, p21
**Shared fallbacks used (7)**: p04, p09, p10, p11, p14, p15, p22
**Total**: 22 analysis dimensions (full parity with Talend)

## Session: 2026-03-17

### [Feature]: Workato parser dual-format support (old flat JSON + new nested recipe.steps)

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` -- added ~200 lines new code, ~15 lines modifications
  - `intodoc/src/intodoc/platforms/workato/__init__.py` -- added `.recipe_function.json` to asset map
  - `intodoc/tests/test_platforms/test_workato.py` -- added 6 new test classes (~150 lines)
  - `intodoc/tests/fixtures/workato_new_format/` -- 8 new fixture files
- **Problem**: Workato parser only handled old export format (flat JSON with `code` blocks). Production Workato exports use nested format with `recipe.steps` arrays. All scanned recipes produced empty components/connections/purpose/tmap_data.
- **Root cause**: Two JSON format variants exist -- old `{code: {keyword, block}}` vs new `{recipe: {trigger, steps}}`. Same for connections: `{connection: {provider}}` vs flat `{provider}`.
- **Change**:
  1. Added `_unwrap(data, key)` static helper -- checks if `data[key]` is a dict wrapper, returns inner object or passes through
  2. Added unwrap calls to 6 read methods: `_parse_recipe`, `_parse_connection`, `_build_recipe_id_index`, `_parse_lookup_tables`, `_parse_properties` (account + project)
  3. Added `_DATAPILL_NEW_RE` regex for `{{...}}` handlebars syntax alongside existing `#{_('...')}` old syntax
  4. Added `_connection_id_index` (int -> str) for numeric connection_id lookups in new format
  5. Added `_resolve_connection_ref` helper -- resolves by numeric ID, provider name, or provider-to-conn map
  6. Added format branching in `_parse_recipe`: detects new format via `data.get("trigger")` or `data.get("steps")`, calls new walker; else falls through to existing `_walk_code_blocks`
  7. Added `_walk_trigger` method -- extracts trigger as first Component from `recipe.trigger`
  8. Added `_walk_steps` method (~100 lines) -- recursive walker for `steps[]` format handling action, if, foreach/repeat, monitor, catch, stop step types
  9. Added helper methods: `_make_step_component`, `_extract_new_step_params`, `_extract_trigger_params`, `_extract_new_condition_params`
  10. Added `_parse_recipe_functions` method for `*.recipe_function.json` files
  11. Added `.recipe_function.json -> FileType.JOB` to `_ASSET_TYPE_MAP` in `__init__.py`
- **Why this approach**: Format branching in `_parse_recipe` keeps old code path completely untouched. `_unwrap` is a zero-cost operation for old-format files (no wrapper key found, returns same dict). New walker produces identical IR types (Component, Connection, TMapEntry, TRunJobRef) as old walker.
- **Verification**: 227 tests pass (76 Workato-specific: 46 existing unchanged + 30 new). All existing tests pass without modification.

---

## Session: 2026-03-17

### [Feature]: Workato + Generic platform plugins, BaseDistiller refactoring

- **Files changed**:
  - **New: `intodoc/src/intodoc/platforms/base_distiller.py`** (~200 lines): Extracted generic chunking logic from TalendDistiller. Contains `distill()`, `_split_chunk()`, `_make_chunk()`, `_filter_manifest()`, `_match_connections_for_jobs()`, `_match_routines_for_jobs()`, `_dedup_contexts()`, `_compact_contexts()`, `_extract_defaults()`, `_print_report()`. Abstract hook `_distill_job()` for subclass overrides.
  - **New: `intodoc/src/intodoc/platforms/generic/__init__.py`** (~110 lines): GenericPlugin with extension-based file classification (`_EXT_MAP`), `_SKIP_DIRS`, `_SKIP_EXTS`. Prompt resolution: `generic/prompts/` -> `_shared/prompts/` fallback chain.
  - **New: `intodoc/src/intodoc/platforms/generic/parser.py`** (~260 lines): GenericParser groups files by directory, builds jobs from sub-directories, extracts contexts from .properties/.env files, detects connections from config files (host/port/database keys), extracts code files as routines.
  - **New: `intodoc/src/intodoc/platforms/generic/file_rules.yaml`** (~80 lines): Extension-based classification rules.
  - **New: `intodoc/src/intodoc/platforms/generic/best_practices.yaml`** (~120 lines): Generic integration project best practices.
  - **New: `intodoc/src/intodoc/platforms/workato/__init__.py`** (~100 lines): WorkatoPlugin extends GenericPlugin. `_ASSET_TYPE_MAP` classifies 14 Workato asset types (recipe, connection, lookup_table, etc.).
  - **New: `intodoc/src/intodoc/platforms/workato/parser.py`** (~530 lines): Full WorkatoParser with recursive block walker (`_walk_code_blocks`), datapill extraction (`_DATAPILL_RE`), manifest building, 8-phase parse. Handles all keyword types: trigger, action, if/elsif/else, foreach, repeat, monitor, catch, stop. Provider-to-connection mapping for manifest building.
  - **New: `intodoc/src/intodoc/platforms/workato/distiller.py`** (~60 lines): WorkatoDistiller extends BaseDistiller. Strips UI metadata (uuid, number, position, schema fields, etc.).
  - **New: `intodoc/src/intodoc/platforms/workato/file_rules.yaml`** (~60 lines): Workato asset type patterns.
  - **New: `intodoc/src/intodoc/platforms/workato/best_practices.yaml`** (~572 lines): Comprehensive Workato platform context: 13 concepts, 32 component types, 10 anti-patterns (WAP-001 to WAP-010), 8 connection types, error handling patterns, naming conventions.
  - **New: `intodoc/src/intodoc/platforms/_shared/prompts/`** (12 files): Platform-agnostic analysis prompts (p01, p02, p03, p04, p05, p06, p09, p10, p11, p14, p15, p22). Adapted from Talend prompts with all platform-specific references generalized.
  - **New: `intodoc/src/intodoc/platforms/workato/prompts/`** (8 files): Workato-specific prompt overrides (p01_inventory, p02_execution, p03_data_flows, p05_external_systems, p06_error_handling, p08_custom_code, p13_naming, p19_migration_assessment).
  - **Modified: `intodoc/src/intodoc/platforms/registry.py`**: Added "generic" and "workato" to `_PLATFORM_MAP`.
  - **Modified: `intodoc/src/intodoc/platforms/talend/distiller.py`**: Refactored to extend BaseDistiller. Keeps only Talend-specific filtering logic.
  - **Modified: `mcp-server/src/mcp_server/services/scanner_service.py`**: Updated `get_analysis_prompts()` to merge shared + platform prompts with fallback resolution.
  - **New: `tests/fixtures/generic_sample/`**: src/main.py, config/database.json, config/app.yaml, sql/create_tables.sql.
  - **New: `tests/fixtures/workato_sample/`**: manifest.json, 3 connections, 3 recipes (nested blocks, datapills, call_recipe, monitor/catch), lookup_table, 2 properties.
  - **New: `tests/test_platforms/test_generic.py`**: 45 tests (registry, classify, parse, best_practices).
  - **New: `tests/test_platforms/test_workato.py`**: 48 tests (registry, classify 14 types, parse recipes, datapill extraction, block walker, connections, distiller, best_practices).
- **Problem**: intodoc only supported Talend. No way to scan Workato projects or arbitrary codebases.
- **Root cause**: Platform plugin architecture existed but only Talend was implemented.
- **Change**: Three-tier implementation:
  1. BaseDistiller: Extracted generic chunking from TalendDistiller (zero behavior change, all existing tests pass).
  2. GenericPlugin: Universal fallback -- classifies files by extension, groups by directory, extracts contexts/connections/routines.
  3. WorkatoPlugin(GenericPlugin): Structured JSON parsing for 14 Workato asset types with recursive block tree walking, datapill field-level lineage extraction, and recipe-to-recipe manifest building.
  4. Shared prompts: 12 platform-agnostic prompts in `_shared/prompts/`, with platform-specific override support.
- **Why this approach**: Inheritance chain (PlatformPlugin -> GenericPlugin -> WorkatoPlugin) maximizes code reuse. BaseDistiller extraction enables all platforms to use the same chunking logic. Shared prompts reduce duplication while allowing platform overrides.
- **Verification**: 200 tests pass (93 new + 107 existing), 8 skipped (Talend integration needing real project data). Zero regressions.

---

## Session: 2026-03-16

### [Feature]: Enhanced credential filter with two-layer redaction

- **Files changed**:
  - `intodoc/src/intodoc/config.py` (lines 9-21, 48-50): Added `RedactionLevel` enum (standard/strict/paranoid) and 3 new config fields (redaction_level, redaction_config, redaction_audit)
  - `intodoc/src/intodoc/scanner/credential_filter.py` (full rewrite, ~250 lines): Added FQDN/URL/email patterns (standard), file path patterns (strict), safe domain exclusion (w3.org, eclipse.org, etc.), custom config from `.intodoc-redact.yaml`, SHA-256 audit logging, `is_sensitive_var_name()` method, `RedactionLevel`-aware pattern activation
  - `intodoc/src/intodoc/scanner/post_filter.py` (NEW, ~130 lines): Post-parse structural filter -- walks ScanResult models, redacts MetadataConnection.host (always), database/schema (strict+), file_path (always), context variable values by type/name semantics, component param values via text filter catch-all
  - `intodoc/src/intodoc/cli.py` (lines 14, 32-75, 80-91): Added `--redaction-level`, `--redaction-config`, `--no-redaction-audit` CLI flags; auto-discovers `.intodoc-redact.yaml` in repo root; calls `filter_scan_result()` after parsing; writes `redaction_report.json`; `_do_scan()` now returns `(result, cred_filter)` tuple
  - `intodoc/tests/test_scanner/test_credential_filter.py` (full rewrite, 37 tests): Tests for all new patterns, redaction levels, variable name sensitivity, audit report, custom config, false positive safety (Talend component names, XML namespaces)
  - `intodoc/tests/test_scanner/test_post_filter.py` (NEW, 14 tests): Tests for MetadataConnection redaction, context variable redaction by type/name/level, paranoid mode, component param filtering, structural preservation
- **Problem**: Real scan output leaked FQDNs (079talend02.keune.com), URLs, email addresses, usernames (activemq_user=admin), file paths, cloud resource IDs through to LLM
- **Root cause**: Original credential_filter.py only had 9 patterns targeting encrypted passwords, IPs, JDBC strings, credential files, and certificates. No FQDN/URL/email/path matching. No post-parse semantic filtering.
- **Change**: Two-layer filtering: Layer 1 (raw text) adds FQDN, URL, email patterns at standard level + file paths at strict level, with safe domain exclusion. Layer 2 (post-parse) walks ScanResult models and redacts based on field semantics (connection hosts, password-type vars, infrastructure vars). Three configurable levels. Custom rules via `.intodoc-redact.yaml`. Audit report with category counts + SHA-256 hashes (never raw values).
- **Why this approach**: LLMs process all tokens in context -- "don't read passwords" doesn't prevent data from hitting the provider's servers. Must strip data in Python before serialization. Two layers because some data only becomes sensitive in context (e.g., "admin" is only sensitive when it's the value of activemq_user). Configurable levels because different customers have different compliance requirements.
- **Verification**: 51 tests pass (37 credential_filter + 14 post_filter). Talend component names (tMSSqlInput, tBigQueryOutput) confirmed not redacted. XML namespace domains (w3.org, eclipse.org) confirmed preserved. Audit report confirmed to never contain raw values.

---

## Session: 2026-03-13

### [Feature]: Static customer-facing assessment page

- **Files changed**:
  - `mcp-server/pyproject.toml` (line 20): Added `jinja2>=3.1.0` dependency
  - `mcp-server/src/mcp_server/services/assessment_service.py` (NEW, ~200 lines): AssessmentService with markdown section extraction and Jinja2 HTML rendering
  - `mcp-server/src/mcp_server/templates/assessment.html` (NEW, ~300 lines): Self-contained HTML template with inline CSS, sticky sidebar TOC, responsive layout, print-friendly
  - `mcp-server/src/mcp_server/schemas.py` (lines 196-201): Added `AssessmentGenerateResponse` schema
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 7-8): Added `import logging` + `logger` (was missing, caused NameError on usage posting failure)
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 20): Added `AssessmentGenerateResponse` to schema imports
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 340-350): Added assessment generation hook in `_run_generate_background()` after RAG push
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 818-860): Added `POST /generate-assessment` and `GET /assessment` endpoints
- **Problem**: No customer-facing output -- platform was developer-only
- **Root cause**: No service existed to produce a readable, self-contained HTML page from generated docs
- **Change**: Created `AssessmentService` (sibling to `PdfService`) that reads `00_EXECUTIVE_SUMMARY.md` (required) and `11_MIGRATION_ASSESSMENT.md` (optional), extracts curated sections, renders them into a self-contained HTML page via Jinja2. Auto-regenerates after doc generation in `_run_generate_background()`. Two new endpoints: generate + serve.
- **Why this approach**: New service (not extending PdfService) because web layout needs differ from print (responsive sidebar, severity badges vs A4 pages). Jinja2 template for conditionals/loops. Self-contained HTML (no CDN/JS) for offline use. Graceful degradation if doc 11 missing.
- **Verification**: Tested section extraction (7/7 sections from doc 00, 4/4 from doc 11). Tested full HTML generation (1036 lines output). Tested graceful degradation (doc 11 missing: migration sections omitted, page still works). Also fixed pre-existing bug: `logger` was used but not imported in projects.py.

---

## Session: 2026-03-12 (Round 3)

### [Feature]: Consolidate 25 reference docs to 12 + improve doc generation quality

- **Files changed**:
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines 291-580): Updated `agent_ref_map` to 3 bundles (Core/Business+Ops/Patterns+User), rewrote entire prompt removing Phase 2.5, added depth targets and narrative rules
  - `mcp-server/src/mcp_server/services/scanner_service.py` (lines 729-776): Updated `_AGENT_REFERENCE_DOCS` mapping from 10 groups to 3 (Core/Business+Ops/Patterns+User)
  - `.agent/system/mcp-workflow.md` (lines 134-221): Updated Phase 3 agent table (4/3/5 split), added depth targets table, narrative rules, updated output structure
  - `intodoc/reference-docs/` (25 files → 12 files): Consolidated reference docs per mapping
- **Problem**: Generated docs were "slim and shallow" after Round 2 optimizations. Phase 2.5 condensation was too aggressive (cutting detail). 25 docs spread too thin across 10 agents. No depth targets. Agent workload imbalanced (4/8/13).
- **Root cause**: (1) Phase 2.5 regex-based condensation stripped important detail before Phase 3 agents could use it. (2) 25 docs across 10 agents meant each agent wrote very little. (3) No minimum line counts. (4) Unbalanced workload.
- **Changes**:
  - Removed Phase 2.5 condensation entirely — Phase 3 agents now read FULL `_analysis/{group}.md` files
  - Consolidated 25 reference docs → 12 (merging related docs: e.g. error handling + scheduling + known issues → 05_OPERATIONS_RISK)
  - Rebalanced Phase 3 agents: Core (4 docs), Business+Ops (3 docs), Patterns+User (5 docs)
  - Added depth targets: 200-800+ min lines per doc type
  - Added narrative rules: "never summarize when you can enumerate", require job name references
  - Added auto-fallback for large projects (>400KB analysis)
  - Updated scanner_service.py `_AGENT_REFERENCE_DOCS` and `get_reference_docs()` to match new 3-group structure
- **Why this approach**: Consolidation reduces overhead (fewer agent spawns), ensures each doc has enough source material for depth, and the depth targets + narrative rules create measurable quality standards.
- **Verification**: Pending — restart MCP server and run test doc generation.

---

## Session: 2026-03-12

### [Fix]: Auto-assign creator on ZIP upload project creation

- **Files changed**: `mcp-server/src/mcp_server/api/routes/projects.py` (lines 137-188, `upload_zip` endpoint)
- **Problem**: Non-admin user creates project via ZIP upload tab, but can't see it afterward. `list_projects` filters by `project_assignments`, but `upload_zip` never created an assignment for the creator.
- **Root cause**: The `add_repo` endpoint had auto-assign logic (added earlier), but `upload_zip` was missed — it lacked both `request: Request` parameter and the INSERT into `project_assignments`.
- **Change**: Added `request: Request` parameter to `upload_zip` signature. Added auto-assign block (same pattern as `add_repo`): extracts `auth_payload` from `request.state`, inserts into `project_assignments` with `ON CONFLICT DO NOTHING`.
- **Why this approach**: Mirrors the existing `add_repo` pattern exactly. Both project creation paths now auto-assign the creator.
- **Verification**: MCP server restarted via systemctl. Also manually assigned stale `test` project to joshitachu (user_id=6) in DB.

---

## Session: 2026-03-11 (Round 2)

### [Perf]: Round 2 doc generation optimizations — Phase 2.5 condensation + group splitting + pre-bundled assets

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (methods: `_format_chunk_markdown` new, `_preprocess_scan_data` rewritten, prompt rewritten)
- **Problem**: Run #2 showed Phase 2 improved (33→18 min) but Phase 3 regressed (21→29 min). Analysis files totaled 688KB — too large for inline passing. MCP tools unavailable on VPS (empty settings.json), so agents wasted turns hunting for format guidance. USA group (90 jobs) was a Phase 2 bottleneck. Core+Data agent had the 7 heaviest docs.
- **Root cause**: (1) 688KB analysis couldn't pass inline, all 3 Phase 3 agents read same 688KB file. (2) No MCP tools = no `get_analysis_prompts()` (223KB) or `get_reference_docs()` (692KB). (3) USA group too large for single agent. (4) Unbalanced Phase 3 workload.
- **Changes**:
  - **Fix 1 — Pre-bundled reference docs**: `_preprocess_scan_data()` now bundles reference docs per Phase 3 agent: `_ref_core.md` (111KB), `_ref_data_ops.md` (269KB), `_ref_config_user.md` (313KB). Eliminates `get_reference_docs()` MCP dependency.
  - **Fix 2 — Pre-bundled analysis prompts**: Concatenates all 22 prompt files (223KB) into `_preprocess/_analysis_prompts.md`. Eliminates `get_analysis_prompts()` MCP dependency.
  - **Fix 3 — Phase 2.5 analysis condensation**: New prompt phase between Phase 2 and Phase 3. Orchestrator reads all analysis files, extracts per-agent relevant sections, writes `_briefing_core.md`, `_briefing_data_ops.md`, `_briefing_config_user.md` (~100-150KB each instead of 688KB). Uses Python regex extraction.
  - **Fix 4 — Large group splitting**: Groups with >40 jobs auto-split into `{group}_part1.md` / `{group}_part2.md`. Phase 2 spawns one sub-agent per part, merges into single analysis. Extracted `_format_chunk_markdown()` static helper for chunk processing reuse.
  - **Fix 5 — Rebalanced Phase 3 agents**: Core (4 heavy docs: 00-03), Data+Ops (8 medium docs: 04,07-11,16,17), Config+User (13 light docs: 05,06,12-15,18-23,25). Moved DATA_FLOWS and SCHEMA_DICTIONARY out of Core.
- **Why this approach**: Each fix targets a specific measured bottleneck from Run #2. Pre-bundling eliminates MCP dependency. Condensation reduces Phase 3 input by ~4x. Group splitting parallelizes the USA bottleneck. Rebalancing equalizes Phase 3 wall-clock time.
- **Verification**: Python syntax check passed. Expected improvement: ~51.5 min → ~29-33 min.

---

## Session: 2026-03-11

### [Fix/Perf]: Doc generation speed optimization + token tracking fix

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (entire file)
- **Problem 1**: Token tracking showed 0 for all generate runs. `result_event.get("cost_usd")` and `result_event.get("total_input_tokens")` returned None because Claude CLI uses different field names.
- **Root cause 1**: Claude CLI result event uses `total_cost_usd` (not `cost_usd`) and nests tokens under `usage{}` (not `total_input_tokens`/`total_output_tokens`).
- **Change 1**: Fixed field extraction: `cost_usd` → `total_cost_usd`, tokens from `result_event.get("usage", {})` including cache tokens, model name from `modelUsage`.
- **Problem 2**: Doc generation took 58 min (target: ~20 min). Sub-agents wasted ~33 min fighting JSON parsing errors, cascading Read failures, and redundant file I/O.
- **Root cause 2**: Raw job JSONs (26K-73K tokens) too large for Read tool (25K limit). Cascading sibling failures when one Read fails in a parallel batch. 5 Phase 3 agents each independently reading same analysis files.
- **Change 2**:
  - Added `_preprocess_scan_data()` method: reads raw scan data via ScannerService, writes structured markdown per group to `_preprocess/{group}.md`
  - Phase 2 prompt: agents read `_preprocess/` files instead of calling `fetch_chunk()` on raw JSONs
  - Phase 3 prompt: orchestrator reads analysis files and passes content INLINE to 3 agents (down from 5), agents don't read files from disk
  - Phase 4: cleanup now also deletes `_preprocess/` directory
  - Added FILE READING GUIDANCE section: max 3 parallel Reads, use Bash for large files, use python3 for JSON extraction
- **Why this approach**: All improvements target wasted I/O time, not analytical depth. Same data, same quality, fewer round-trips.
- **Verification**: Python syntax check passed, import verification passed. Expected improvement: ~58 min → ~20-25 min.

---

### [Feature]: Doc gen + deep dive token persistence via RAG backend

- **Files changed**:
  - `mcp-server/src/mcp_server/services/rag_bridge.py` -- added `post_usage()` method (fire-and-forget POST to RAG backend)
  - `mcp-server/src/mcp_server/api/routes/projects.py` -- call `bridge.post_usage(interface="doc_generation", ...)` after doc gen; intercept usage SSE events in deep_dive_query and persist via `bridge.post_usage(interface="deep_dive", ...)`
- **Problem**: Doc generation extracted cost/tokens from Claude CLI but never saved to QueryUsage table. Deep dive streamed usage to frontend but didn't persist.
- **Root cause**: No mechanism existed to push usage data from the KB MCP server to the RAG backend's QueryUsage table.
- **Change**: RagBridge now has `post_usage()` that POSTs to `{base_url}/api/v1/usage/record` with Bearer auth. Doc generation calls it after `complete_generate_run`. Deep dive SSE stream intercepts `type="usage"` events and calls it.
- **Why this approach**: Fire-and-forget with warning log on failure -- usage recording failure shouldn't break doc generation or deep dive streaming.
- **Verification**: Frontend build passes, 236 RAG backend tests pass.

---

## Session: 2026-03-10

### [Feature]: Per-project usage widget + ROADMAP.md

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` -- added `ProjectUsageResponse` model
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` -- added `GET /projects/{id}/usage` endpoint with SQL aggregation
  - `knowledge_base/dashboard/src/types.ts` -- added `ProjectUsage` interface
  - `knowledge_base/dashboard/src/api/client.ts` -- added `getProjectUsage()` method
  - `knowledge_base/dashboard/src/hooks/useProjects.ts` -- added `useProjectUsage()` hook
  - `knowledge_base/dashboard/src/components/ProjectSettings.tsx` -- added usage summary widget (tokens, cost, run counts)
  - `ROADMAP.md` (root) -- created master roadmap tracking file
- **Problem**: No per-project usage visibility in KB dashboard; no master roadmap file existed
- **Root cause**: Feature not yet implemented (Phase 1.3 gap)
- **Change**: Added usage API endpoint that aggregates GenerateRun/ScanRun stats via SQL, and a 4-tile widget in ProjectSettings showing generate runs, scan runs, total tokens, and total cost. Created ROADMAP.md with full 3-phase product roadmap with checkboxes.
- **Why this approach**: Reuses existing GenerateRun token/cost fields; SQL aggregation is efficient; widget fits naturally in the Settings tab
- **Verification**: TypeScript type check passed (`tsc --noEmit`), Python syntax verified

---

## 2026-03-03 -- Fix stuck "generating" status in dashboard

**What was done:**
- Added `refetchInterval: 10_000` to `useProjects()` hook so project list polls every 10s
- Added `POST /mcp/reset-status` backend endpoint to reset stuck generating/scanning projects
- Added `ResetStatusRequest` schema to `schemas.py`
- Added `resetStatus()` to API client and `useResetStatus()` mutation hook
- Added red "Reset" button to ActionButtons (visible only when status is generating/scanning)
- The reset endpoint sets project status to `error`, marks running GenerateRun/ScanRun records as `failed`
- Updated `mcp-server.md` and `dashboard.md` to reflect new endpoint and features

**Files changed:** 7 files (5 source, 2 docs)

**Lessons learned:**
- SSE-only UI updates are fragile -- always pair with polling as a fallback
- Stuck states need an explicit user escape hatch (reset button) since disabling all actions locks the user out

---

## 2026-02-19 -- Documentation Restructuring

**What was done:**
- Restructured platform documentation following AI Jason's .agent folder pattern
- Rewrote `CLAUDE.md` from 480 lines to ~65 lines (concise rules + pointers)
- Moved MCP 4-phase workflow to `.agent/system/mcp-workflow.md` (no content lost)
- Rewrote `.agent/README.md` with smart "when to read what" index
- Updated `.agent/system/architecture.md` to be platform-generic (removed vaxxinova-specific metrics)
- Created new system docs: `tech-stack.md`, `mcp-server.md`, `dashboard.md`
- Created SOPs: `run-doc-generation.md`, `add-new-project.md`, `setup-dev-environment.md`, `troubleshooting.md`
- Created ADR: `decisions/001-mcp-over-cli.md`
- Populated `.claude/commands/update_doc.md` (/update-doc command)
- Created `.claude/PROGRESS_LOG.md` and `.claude/PROJECT_MEMORY.md`

**Files changed:** 15 files (3 rewrites, 1 update, 11 new)

**Lessons learned:**
- CLAUDE.md should be concise (~100 lines max) with pointers to detailed docs
- Smart README index ("when to read what" table) helps Claude Code find relevant docs fast
- All platform docs should be generic -- use `{project_id}` instead of hardcoded names
- SOPs with numbered steps and actual commands are more actionable than prose descriptions
