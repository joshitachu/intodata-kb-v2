# Progress Log

Session log for the intodoc platform. Every session should add an entry documenting
what was done, what changed, and lessons learned.

## Session: 2026-04-10

### [Perf]: Topology graph endpoint -- stop synthesizing REFERENCE:* hub edges in legacy-scan fallback

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `_FALLBACK_INTERNAL_COMPONENTS` (lines ~990-1006): added the tRunJob-family
    diagnostics that were missing from the internal-components set:
    `tLogCatcher`, `tStatCatcher`, `tFlowMeter`, `tFlowMeterCatcher`,
    `tChronometerStart`, `tChronometerStop`, `tAssert`, `tAssertCatcher`.
    (`tRunJob`, `tPrejob`, `tPostjob`, `tWarn`, `tDie`, `tLogRow` were already
    present but the heuristic still leaked REFERENCE:* names through the
    suffix-stripping path -- see below.)
  - `_derive_systems_from_job_json` (lines ~1087-1105) Talend branch: added a
    belt-and-braces filter `and not system.startswith("REFERENCE")` so any
    component that produces a `REFERENCE:*` system name through the
    suffix-stripping fallback is dropped instead of becoming a fake hub.
    Also capped the result at `sorted(systems)[:6]` -- more than this means
    the heuristic is matching noise. Modern scans populate
    `job_external_systems` explicitly and bypass this path entirely, so the
    cap is a noise-floor on the legacy fallback only.
- **Problem**: `/mcp/projects/scherpenhuizen-talend/graph` returned ~37
  connection nodes and ~256 job→connection edges even though the on-disk
  manifest had `job_connections_used: {}`, `connection_used_by: {}`, and an
  absent `job_external_systems` (legacy scan). The synthesized result was
  hub-shaped: Oracle (29 jobs), `REFERENCE:sendToMonitoringMOM` (27 jobs),
  `REFERENCE:Trigger_Next_Interface_92` (18 jobs). The frontend topology
  view stuttered on hover/toggle because every hover dimmed ~30 adjacencies
  and dagre had to lay out 80+ nodes / 271 edges (18x more than the manifest
  hinted at). Most of the edges were noise -- the `REFERENCE:*` "systems" are
  internal Talend job-call wiring, not real external systems.
- **Root cause**: `_fallback_talend_system` strips a leading `t` and then
  trailing `Input/Output/Connection/...` suffixes from the component type to
  guess a system name. For `tRunJob`-family components the payload that
  bubbles through is the called-job reference (`REFERENCE:sendToMonitoringMOM`,
  `REFERENCE:Trigger_Next_Interface_92`, etc.), and the fallback was treating
  those as foreign systems. The internal-components set was incomplete -- it
  had `tRunJob` itself but not the diagnostic siblings -- and the function had
  no defense against `REFERENCE:*` payloads that leaked through.
- **Change**: Two layers. (1) Expanded `_FALLBACK_INTERNAL_COMPONENTS` so the
  diagnostic family returns `None` from `_fallback_talend_system`. (2) Added
  the `not system.startswith("REFERENCE")` filter directly in
  `_derive_systems_from_job_json` for Talend, plus the `[:6]` cap on the
  fallback result to prevent any future heuristic mismatch from generating
  hub-shaped fake-system noise.
- **Why this approach**: Surgical -- touches only the runtime-derivation
  fallback path used by legacy scans missing `job_external_systems`. Real
  scans bypass this path entirely. Real Talend external systems (Oracle,
  MSSql, FTP, SAP, Salesforce, etc.) still surface in the topology -- only
  the synthetic `REFERENCE:*` noise from `tRunJob` payloads disappears. If
  the user wants the REFERENCE wiring back later, the scanner should
  populate `job_external_systems` explicitly via tRunJob -> called-job
  tracing -- that's a scanner feature, not a graph-rendering one.
- **Verification**: Restart the systemd `mcp-server` (editable install picks
  up the Python change), then `GET /mcp/projects/scherpenhuizen-talend/graph`
  with the JWT cookie. Expect ~10-15 connections (was ~37) and zero entries
  starting with `REFERENCE:`. Smoke `GET /mcp/projects/esbmain/graph` -- its
  manifest already populates `job_external_systems`, so its shape is
  unchanged (bypasses the runtime fallback entirely). The matching frontend
  fix is logged in `RAG_pipeline/.claude/PROGRESS_LOG.md` (Perf round 3).

### [Fix]: Topology job detail panel -- use real `**Purpose**` lines from inventory, drop AI summarizer

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
    - Added `_PURPOSE_LINE_RE` regex (line ~1220)
    - Added `_extract_job_purpose_from_inventory(project_id, job_name)` helper
    - Added `_extract_job_doc_sections(project_id, job_name)` helper
    - Rewrote `get_job_detail()` to use the new helpers; removed the per-`.md`
      `## `-split loop and the `SummaryService.load_summaries(project_id)` call.
      Summary now prefers the structured `**Purpose**:` line from
      `02_ARCHITECTURE_INVENTORY.md`, falling back to `_derive_job_summary` for
      jobs missing from the inventory.
- **Problem**: Clicking a job in `/topology` showed (a) the templated
  `"Integration job connecting to {systems}."` placeholder or an unrelated
  paragraph as the "What This Job Does" summary, and (b) "From the Docs"
  returned the entire `## Job Architecture Details` section (~50 jobs, thousands
  of lines) for every job click on `esbmain`.
- **Root cause**:
  1. `_derive_job_summary` substring-grepped paragraphs for the job name. The
     correct `**Purpose**: ...` paragraph in `02_ARCHITECTURE_INVENTORY.md`
     never contains the job name (the name lives in the preceding `#### {Name}`
     heading), so it was always skipped. The first paragraph that did match was
     the bare heading itself, which after `_MD_NOISE_RE` cleanup became too
     short and was discarded -- so we fell into deeper sections or the
     placeholder.
  2. `get_job_detail()` split each `.md` by `## ` only, so any `## ` section
     that mentioned the job name (including the giant inventory section) was
     returned in full.
  3. `SummaryService.load_summaries` had never been triggered for any project
     (`summaries.json` does not exist), so it always returned `{}` -- it was
     dead weight in the request path.
- **Change**:
  - `_extract_job_purpose_from_inventory` finds `#### {JobName}` (any depth
    `##`-`####`), bounds the section by the next sibling-or-higher heading, and
    extracts the `**Purpose**:` line verbatim, trimmed to 2-3 sentences.
  - `_extract_job_doc_sections` walks every `.md` in `docs/` (or
    `docs_staging/`), finds headings whose line starts with the job name, and
    returns each as its own bounded section. Heading is prefixed with the doc's
    pretty stem (e.g. `ARCHITECTURE INVENTORY - sjAccount_OffRamp_DWH`).
  - `get_job_detail()` now: builds `doc_sections` from
    `_extract_job_doc_sections`, computes `summary` as
    `_extract_job_purpose_from_inventory(...) or _derive_job_summary(...) or ""`,
    and no longer touches `SummaryService`.
  - `summary_service.py`, `/generate-summaries` route, and the `ollama` dep are
    untouched -- left dormant for any future opt-in use.
- **Why this approach**: The doc generator already writes the brief, accurate
  `**Purpose**:` line during the 4-phase workflow; the right fix is to parse it
  out of the structured markdown rather than re-derive it heuristically (or
  worse, regenerate it via an LLM). Zero LLM calls, zero changes to the
  doc-generation pipeline, no schema changes, no frontend changes.
- **Verification**:
  - Direct helper smoke test (no auth):
    - `_extract_job_purpose_from_inventory('esbmain', 'Account_Subscription')`
      -> `"Manages webhook subscriptions for Account entities in the Business Central API v2.0."`
    - `_extract_job_purpose_from_inventory('esbmain', 'sjAccount_OffRamp_DWH')`
      -> `"Writes Customer golden record from CMM to BigQuery."`
    - `_extract_job_doc_sections('esbmain', 'sjAccount_OffRamp_DWH')` ->
      1 section, 531 chars (was previously the whole inventory section).
  - End-to-end via `get_job_detail()` returns the same summary and bounded
    sections.
  - Sampled 8 jobs across `esbmain`: 6 returned real Purpose lines; 2 returned
    `None` (not in the inventory) and correctly fell through to the heuristic.
  - Workato project (different inventory layout, recipes use
    `### N.M Recipe X: name` headings) returns `None` from the structured
    extractor and falls back to `_derive_job_summary` -- no crash.
  - `sudo systemctl restart mcp-server` -> `Active: active (running)`,
    no import errors in `journalctl -u mcp-server`.

### [Fix]: Topology view "Not Found" -- stale mcp-server binary + mislocated data

- **Files changed**:
  - `/etc/systemd/system/mcp-server.service` (replaced with repo copy from `deploy/mcp-server.service`)
  - `/home/intodata/intodata-kb-v2/venv` (new symlink -> `/home/intodata/intodata-kb/venv`)
  - `/home/intodata/intodata-kb-v2/knowledge_base/intodoc-output/` (populated from old path; 24 projects)
  - `/home/intodata/intodata-kb-v2/knowledge_base/output/` (populated from old path; 19 projects)
  - `/home/intodata/intodata-kb/knowledge_base/{intodoc-output,output}` (replaced with symlinks back to v2 as a safety net)
  - `/home/intodata/intodata-kb/venv` (`pip install ollama>=0.4.0` -- new dep added in v2 pyproject.toml but never installed)
- **Problem**: `/topology` page in the RAG frontend always rendered "Not Found / Retry". Logs showed `GET /mcp/projects/esbmain/graph HTTP/1.1 404 Not Found` while `GET /mcp/projects` returned 200. The 404 detail was FastAPI's default `{"detail":"Not Found"}` (route-not-registered), not the endpoint's own `"No scan data for project: ..."` body.
- **Root cause**: Two stacked issues:
  1. The installed systemd unit at `/etc/systemd/system/mcp-server.service` still pointed at the OLD checkout (`/home/intodata/intodata-kb/...`) and had not been updated when the v2 checkout was created. The `mcp-server` PID had been running uninterrupted since 2026-03-24, predating the topology feature commit `4a45b4d`. The `get_project_graph` route did not exist in the in-memory code, so FastAPI returned its default 404.
  2. All scan/doc data the user generated lived under the OLD path (`intodata-kb/knowledge_base/{intodoc-output,output}`) because the running server's working directory was the OLD knowledge_base. Just restarting against the v2 unit would have flipped 404 -> "No scan data for project" until the data was moved.
- **Change**:
  1. Moved scan + docs data from `intodata-kb/knowledge_base/{intodoc-output,output}` to `intodata-kb-v2/knowledge_base/{intodoc-output,output}`. Left symlinks behind at the old paths so any stragglers still resolve.
  2. Created `intodata-kb-v2/venv` as a symlink to the existing `intodata-kb/venv`. The existing venv already has `mcp_server` editable-installed pointing at the v2 source tree, so the symlink gives the new systemd unit a working interpreter without rebuilding the venv.
  3. Installed `ollama>=0.4.0` into the existing venv -- new dep added to v2 `mcp-server/pyproject.toml` (used by `services/summary_service.py:13`) but never `pip install`-ed. Service was crash-looping with `ModuleNotFoundError: No module named 'ollama'` until this was fixed.
  4. Copied `deploy/mcp-server.service` to `/etc/systemd/system/mcp-server.service`, ran `systemctl daemon-reload` and `systemctl restart mcp-server`. Service is now running from `/home/intodata/intodata-kb-v2/venv/bin/python -m mcp_server --serve --port 8002` with `WorkingDirectory=/home/intodata/intodata-kb-v2/knowledge_base`.
- **Why this approach**: Symlinking the venv was much faster than rebuilding (would have required pulling weasyprint + system libs), and the existing venv's editable install already pointed at v2 source -- so all the topology code is loaded transparently. Moving (not copying) the data ensures a single source of truth going forward; the symlinks back to the old path are a temporary safety net.
- **Verification**:
  - `systemctl status mcp-server` -> `Active: active (running)` from `/home/intodata/intodata-kb-v2/venv/bin/python ...`
  - `curl http://localhost:8002/openapi.json` lists `/mcp/projects/{project_id}/graph` (it didn't before)
  - `curl http://localhost:8002/mcp/projects/esbmain/graph` -> 401 (auth required) instead of the previous 404 -- proves the route is now registered
  - Direct invocation with env loaded: `await get_project_graph('esbmain')` returns `project_id=esbmain`, 6 connections, 21 job_metadata entries
  - `config.intodoc_output_dir` resolves to `/home/intodata/intodata-kb-v2/knowledge_base/intodoc-output` and `esbmain/scan/manifest.json` exists there

## Session: 2026-03-30

### [Fix]: Template creation 500 error (MissingGreenlet)

- **Files changed**: `mcp-server/src/mcp_server/api/routes/templates.py` (lines 8, 44)
- **Problem**: `POST /mcp/templates/` returned 500 with `MissingGreenlet` exception
- **Root cause**: `_template_to_response()` accessed `t.versions` (lazy-loaded relationship) in async context. SQLAlchemy async sessions cannot lazy-load; they require explicit eager loading via `selectinload`. The `create_template` path didn't eager-load versions, so accessing `t.versions` triggered the error.
- **Change**: Added `from sqlalchemy import inspect as sa_inspect`. Changed condition from `if lv is None and t.versions` to `if lv is None and "versions" in sa_inspect(t).dict` -- checks if the relationship is already loaded before accessing it. If not loaded, `latest_version` stays `None`.
- **Why this approach**: Idiomatic SQLAlchemy pattern for async-safe relationship access. Avoids try/except, doesn't require changing every caller to pass `latest_version`, and works for any future endpoint that might not eager-load versions.
- **Verification**: Endpoints using `selectinload(Template.versions)` (list, get) still populate `latest_version`. The `create_template` endpoint returns `latest_version: null` without error.

## Session: 2026-03-24

### [Feature]: Transparency Layer -- Scoring Pipeline + Template Management & Swapping

Full implementation of the template management system and scoring pipeline enhancement across 6 phases (A-F). This is the largest single feature addition to the platform.

#### Phase A: Scoring Pipeline (New Phase 2.6)

- **Files created**:
  - `mcp-server/src/mcp_server/templates/scoring-rubric.md` -- 8 dimensions x 5 levels scoring rubric
  - `mcp-server/src/mcp_server/templates/anti-patterns.md` -- 29 anti-patterns across 6 categories (AP-SEC, AP-ERR, AP-DQ, AP-PERF, AP-ARCH, AP-OPS)
  - `mcp-server/src/mcp_server/templates/red-flags.md` -- 28 red flags across 5 categories
  - `mcp-server/src/mcp_server/templates/security-checklist.md` -- 7-category structured security review
  - `mcp-server/src/mcp_server/templates/pattern-library.md` -- 15 known good patterns
- **Files modified**:
  - `mcp-server/src/mcp_server/services/claude_service.py` -- Added Phase 2.6 (Score & Assess) to mega-prompt, scoring template bundling to `_preprocess_scan_data()`, `_assessment/` directory creation, Phase 3 now reads scorecard
- **Change**: New scoring sub-agent between Phase 2.5 and Phase 3 reads all analysis files + scoring templates, produces `_assessment/scorecard.md` with 8-dimension quantitative scores + evidence. Phase 3 agents reference scores in generated docs.

#### Phase B: Database Foundation

- **Files created**:
  - `mcp-server/src/mcp_server/db/template_models.py` -- 4 SQLAlchemy models: Template, TemplateVersion, TemplateProfile, ProfileAssignment
  - `mcp-server/src/mcp_server/services/template_service.py` -- Full CRUD for templates, versions, profiles, assignments (diff, rollback, clone, set-default)
  - `mcp-server/src/mcp_server/api/routes/templates.py` -- 20+ REST endpoints for template/version/profile/assignment management
  - `mcp-server/scripts/seed_templates.py` -- Idempotent migration of filesystem templates to DB
  - `mcp-server/alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` -- Async Alembic setup
  - `mcp-server/alembic/versions/001_add_template_management.py` -- Migration for 4 new tables + 2 new columns
- **Files modified**:
  - `mcp-server/src/mcp_server/db/models.py` -- Added `template_profile_id` (INT FK) and `template_snapshot` (JSONB) to GenerateRun
  - `mcp-server/src/mcp_server/db/__init__.py` -- Exports for new models
  - `mcp-server/src/mcp_server/schemas.py` -- 15+ new Pydantic schema classes + extended GenerateRequest/GenerateRunResponse
  - `mcp-server/src/mcp_server/api/app.py` -- Registered template/profile/generate_runs routers
  - `mcp-server/pyproject.toml` -- Added `alembic>=1.13.0`

#### Phase C: Template Resolver + Pipeline Integration

- **Files created**:
  - `mcp-server/src/mcp_server/services/template_resolver.py` -- Resolution cascade: explicit profile -> project-scoped default -> platform default -> filesystem fallback
- **Files modified**:
  - `mcp-server/src/mcp_server/services/claude_service.py` -- `_preprocess_scan_data()` now accepts `template_profile_id`, returns `(groups, snapshot)` tuple, uses DB templates with filesystem fallback
  - `mcp-server/src/mcp_server/api/routes/projects.py` -- Passes `template_profile_id` through generate endpoint to background task
  - `mcp-server/src/mcp_server/services/project_registry.py` -- `complete_generate_run()` accepts template fields

#### Phase D: Dashboard -- Template Catalogue + Editor

- **Files created**:
  - `dashboard/src/components/templates/TemplateCatalogue.tsx` -- Filterable template grid with category/platform/search filters
  - `dashboard/src/components/templates/TemplateEditor.tsx` -- Inline editor with version selector, required commit messages
  - `dashboard/src/components/templates/VersionTimeline.tsx` -- Version history with colored unified diff viewer
  - `dashboard/src/hooks/useTemplates.ts` -- 12 React Query hooks for template APIs
- **Files modified**:
  - `dashboard/src/api/client.ts` -- ~20 new API methods for templates/versions/profiles/assignments
  - `dashboard/src/types.ts` -- Template, TemplateVersion, TemplateProfile, ProfileAssignment, ProfileDetail interfaces
  - `dashboard/src/App.tsx` -- Top-level nav (Projects/Templates/Profiles/Docs), template editor layout

#### Phase E: Dashboard -- Profiles & Swapping

- **Files created**:
  - `dashboard/src/components/templates/ProfileManager.tsx` -- Profile list/create/clone UI, set-default, platform filter
  - `dashboard/src/components/templates/ProfileDetail.tsx` -- Per-template version assignment with add-override dropdown
- **Files modified**:
  - `dashboard/src/components/ActionButtons.tsx` -- Profile selector dropdown next to Generate button
  - `dashboard/src/components/RunHistory.tsx` -- Profile column showing P#{id} or "default"
  - `dashboard/src/hooks/useProjects.ts` -- `useGenerate` accepts `templateProfileId`

#### Phase F: A/B Comparison + Document Catalogue

- **Files created**:
  - `mcp-server/src/mcp_server/api/routes/generate_runs.py` -- Template snapshot + run comparison endpoints
  - `dashboard/src/components/templates/ABComparison.tsx` -- Run selector, metrics comparison table, template diff display
  - `dashboard/src/components/templates/DocumentCatalogue.tsx` -- Auto-generated reference of 12 doc types with template links
- **Files modified**:
  - `dashboard/src/App.tsx` -- Added "A/B Compare" tab, "Docs" top-nav page

- **Why this approach**: Templates stored in DB for versioning and admin visibility. Full-copy variants (not section overrides) for simplicity. Per-platform template sets. Filesystem fallback is permanent for backward compatibility. Resolution cascade gives flexibility (explicit profile > project default > platform default > filesystem).
- **Verification**: `npx tsc --noEmit` passes clean. All new components type-check. Backend Python files syntax-verified.

---

## Session: 2026-03-20

### [Fix]: Workato Scanner Format C Support -- 6 Field Mismatch Bugs

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` (6 bug fixes across classification, parsing, and step walking)
  - `intodoc/tests/test_platforms/test_workato.py` (20+ new tests for Format C)
  - `intodoc/tests/fixtures/workato_nested/recipes/nightly_sync/recipe.json` (new fixture)
  - `intodoc/tests/fixtures/workato_nested/recipes/nightly_sync/steps.json` (new fixture)
  - `intodoc/tests/fixtures/workato_nested/connections/amazon_s3/connection.json` (new fixture)
- **Problem**: Workato scanner produced critically incomplete output for real-world manifest/package exports (Format C). 4 of 5 recipes had only trigger placeholders, 1 recipe completely missing, 0 of 6 connections detected, no action steps or field mappings extracted.
- **Root cause**: Parser only handled Format A (code blocks) and Format B (inline trigger+steps). Format C uses different field names (`application` vs `provider`, `action` vs `name`, `authentication_type` vs `authorization_type`, `conditional` vs `if`, `repeat_for_each` vs `foreach`) and wraps steps.json in a dict.
- **Changes**:
  1. Fix 1 (line 626): Unwrap `steps.json` dict `{recipe_id, steps}` in addition to raw list
  2. Fix 2 (lines 861-862): Trigger field fallback `provider||application`, `name||type`; generic `_extract_trigger_params()` iterates all keys
  3. Fix 3 (line 186): Recipe classification accepts `trigger_application`/`action_applications`/`connections_used` as markers
  4. Fix 4 (lines 1087-1088, 920-921): Action step field fallback `provider||application`, `name||action`; generic `_extract_new_step_params()` iterates all keys
  5. Fix 5 (lines 188-193, 430, 443): Connection classification accepts `application` + `authentication_type`/`verified`/`connection_id`; `_parse_connection()` uses fallback chains
  6. Fix 6 (lines 942, 972): Step type aliases `conditional`->`if`, `repeat_for_each`->`foreach`; new handlers for `formula`, `lookup`, `trigger`-as-step
- **Why this approach**: Generic extraction (iterate all keys, `or` fallback chains) handles all known formats AND unknown future variations without hardcoded field lists
- **Verification**: 139 tests pass (20+ new tests covering Format C classification, wrapped steps, new step types, connection field variants)

### [Feature]: Gate Validation + Retries Between Doc Gen Phases

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (2 prompt insertions)
- **Problem**: If a Phase 2 sub-agent produces empty/thin analysis or a Phase 3 agent fails to write a doc, downstream phases proceed anyway, resulting in thin or missing documentation.
- **Root cause**: No validation checkpoints between phases in the orchestration prompt.
- **Change**: Added two gate validation phases to the doc generation prompt:
  - **Phase 2.5** (between Phase 2 and Phase 3): After all Phase 2 sub-agents return, runs `ls -la` on `_analysis/`, checks each group's analysis file exists and is >1KB. Re-spawns failed groups once. Proceeds with warnings if retry fails.
  - **Phase 3.5** (between Phase 3 and Phase 4): After all Phase 3 doc agents return, validates 9 required docs exist and are >500 bytes (00-05, 09-11). Re-spawns only the responsible agent (Core, Business+Ops, or Patterns+User) once. Logs warnings for still-missing docs.
- **Why this approach**: Prompt-only change — no Python logic modifications. Validation runs inside the Claude Code subprocess as part of orchestration. Lightweight (`ls -la` checks) with bounded retries (max 1 per group/agent) to avoid runaway costs.
- **Verification**: `python3 -m py_compile` passes. Full E2E test pending next doc generation run.

### [Feature]: Phase 2 Filesystem Boundary + Scaled Analysis Target

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (3 edits)
- **Problem**: (1) Phase 2 sub-agents sometimes waste 5+ minutes reading raw files from `intodoc-output/` or `data/repos/` despite all data being pre-bundled in `_preprocess/`. (2) Fixed `(target: 15-40KB)` analysis target is excessive for small projects (e.g. 4-job project with ~5KB preprocessed data).
- **Root cause**: (1) No explicit prohibition on filesystem access outside staging dir. (2) Analysis target was a static range regardless of input size.
- **Change**: (1) Added explicit PROHIBITED block to Phase 2 sub-agent instructions: no Glob/Read/Bash outside `{staging_abs}/`, no access to `intodoc-output/`, `data/repos/`, or any other directory. (2) After preprocessing, compute `total_preprocess_bytes` (sum of non-underscore-prefixed .md files in `_preprocess/`), derive `analysis_target_kb = max(5, min(40, total_preprocess_bytes // 2048))`, inject into prompt replacing fixed `15-40KB`.
- **Why this approach**: (1) Sub-agents respect explicit instructions better than implicit expectations. The boundary is clear: everything is in `_preprocess/`, don't look elsewhere. (2) Dividing by 2048 means analysis is ~50% of input size, clamped to 5-40KB range. Small projects get proportional targets; large projects still hit 40KB max.
- **Verification**: `python3 -m py_compile` passes. Visual inspection confirms both changes in correct locations.

### [Feature]: Consolidated Analysis Guide + Dynamic Depth Scaling

- **Files changed**:
  - `intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md` -- NEW (276 lines, 12KB). Consolidated analysis guide replacing 22 separate .txt prompt files for Phase 2 sub-agents.
  - `intodoc/src/intodoc/platforms/talend/prompts/PHASE_3_GUIDE.md` -- NEW (86 lines). Cross-group synthesis guide for Phase 3 agents (exec summary, cross-region comparison, migration assessment).
  - `intodoc/src/intodoc/platforms/_shared/prompts/ANALYSIS_GUIDE.md` -- NEW (125 lines). Platform-agnostic fallback guide.
  - `intodoc/src/intodoc/platforms/_shared/prompts/PHASE_3_GUIDE.md` -- NEW (53 lines). Shared Phase 3 fallback.
  - `mcp-server/src/mcp_server/services/scanner_service.py` -- Modified `get_analysis_prompts()` to prefer ANALYSIS_GUIDE.md; added `get_phase3_guide()` method.
  - `mcp-server/src/mcp_server/services/claude_service.py` -- Modified `_preprocess_scan_data()` to write `_analysis_guide.md` + `_phase3_guide.md`. Rewrote `generate_docs()` prompt: removed 70 lines of inline section definitions (now in guide), replaced hard-coded depth targets with dynamic scaling, added Phase 3 guide reference, deduplicated DO NOT blocks.
- **Problem**: 22 separate prompts bundled into ~222KB caused conflicting instructions (22 "Your task is" statements), wasted ~60% on JSON format specs sub-agents never use, and repeated Talend concepts 5-10x. Hard-coded line minimums (200-800+) don't scale with project size.
- **Root cause**: Prompts designed for old CLI model (separate LLM calls returning JSON) were being bundled verbatim for new MCP model (single markdown output).
- **Change**: (1) Created single ANALYSIS_GUIDE.md per platform (~12KB) preserving all extraction checklists, scoring systems, risk catalogs, and platform knowledge while dropping JSON specs, redundant context, and conflicting framing. (2) Created PHASE_3_GUIDE.md for cross-group synthesis (moved from p10/p17/p19 which were misplaced in Phase 2). (3) scanner_service checks for guide first, falls back to individual .txt files. (4) Prompt now uses dynamic depth: "This project has N jobs across M groups. Scale depth to match content." (5) Inline section definitions removed (guide has them).
- **Why this approach**: 94% token reduction (222KB -> 12KB) for Phase 2 input. Coherent single-document instructions eliminate conflicting "your task is" blocks. Dynamic depth naturally adapts to project size. Original 22 .txt files kept in place for backward compat (legacy CLI, Workato/Generic inheritance).
- **Verification**: Python syntax check passes on both modified files. Guide file sizes confirmed (12KB Talend guide vs 222KB original prompts). Prompt reduced from ~207 to ~127 lines.

## Session: 2026-03-19

### [Fix]: Size-aware bundle splitting for oversized prompt and ref doc files

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (lines 267-350: bundle splitting logic; lines 437-564: orchestrator prompt references)
- **Problem**: After adding Workato p09+p22 prompt overrides, `_analysis_prompts.md` grew to 284.9KB, exceeding Claude's 256KB Read tool file size limit. Phase 2 sub-agents hit `"File content (284.9KB) exceeds maximum allowed size (256KB)"`. Ref doc bundles (`_ref_core.md` at 107KB, `_ref_patterns_user.md` at 169KB) exceeded the 25K token limit (~80KB).
- **Root cause**: Bundle code concatenated ALL prompts (or ALL ref docs per agent) into a single file with no size check. Orchestrator prompt hardcoded single filenames.
- **Change**: (1) Prompt bundle: split at 200KB threshold (`MAX_PROMPT_BUNDLE_BYTES = 200_000`). Writes `_analysis_prompts_1.md`, `_analysis_prompts_2.md` when total exceeds limit, or single `_analysis_prompts.md` when it fits. (2) Ref doc bundles: split at 80KB threshold (`MAX_REF_BUNDLE_BYTES = 80_000`, ~20K tokens). Same suffix pattern. (3) Updated all orchestrator prompt references from hardcoded filenames to glob patterns (`_analysis_prompts*.md`, `_ref_core*.md`, etc.) with instructions to "read ALL matching files".
- **Why this approach**: Threshold-based splitting is simple, deterministic, and forward-compatible as more prompts are added. 200KB is well under the 256KB file limit. 80KB is ~20K tokens, safely under the 25K token limit. Glob patterns let sub-agents discover split files without knowing the count.
- **Verification**: Python syntax check passes. All 6 prompt references updated to glob patterns (grep confirms no remaining hardcoded single-file references).

### [Feature]: Workato-specific p09 and p22 prompt overrides

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/prompts/p09_business_logic.txt` -- NEW. Workato-specific business logic detection prompt replacing shared/Talend fallback.
  - `intodoc/src/intodoc/platforms/workato/prompts/p22_preventive.txt` -- NEW. Workato-specific preventive analysis prompt replacing shared/Talend fallback.
- **Problem**: Shared p09 and p22 prompts contained Talend-specific terminology (tMap expressions, Java routines, tJavaRow, tMap lookup join, tAggregateRow, tBigQuerySQLRow, tLogCatcher, tSendMail, DIE_ON_CHILD_ERROR) that misled the LLM during Workato project analysis.
- **Root cause**: These two prompts were never overridden for Workato during the prompt parity work on 2026-03-18.
- **Change**: Created Workato-specific overrides that: (1) Replace all Talend terms with Workato equivalents (datapill formulas, action input mappings, monitor/catch blocks, call_recipe, lookup_table_entry, etc.). (2) Keep identical JSON output schemas (p09: 7 keys, p22: 7 keys). (3) Add Workato-specific extraction targets (API collection endpoints, property lists, nested recipe steps from sibling steps.json, event topic payloads, callable recipe interfaces). (4) Reference WAP-003 through WAP-010 anti-patterns from best_practices.yaml in p22.
- **Why this approach**: Platform-specific prompts produce dramatically better doc quality than generic prompts with wrong terminology. The shared fallback is fine for genuinely platform-agnostic prompts (p04, p10, p11, p14, p15) but not for business logic or preventive analysis which reference platform components directly.
- **Verification**: (1) Both files discovered by prompt loading (17 Workato overrides total). (2) Zero Talend terms in either file (grep returns no matches). (3) JSON schema keys match shared versions exactly. (4) 126 Workato tests pass (no regressions).

### [Fix]: Doc generation prompt delivery + MCP tool path resolution

- **Files changed**:
  - `mcp-server/src/mcp_server/services/scanner_service.py` (line ~701) -- Changed `platforms_root` from `config.project_root / "intodoc" / "src" / ...` to `Path(intodoc.platforms.__file__).parent`. Uses installed package path instead of filesystem traversal from project_root.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~286) -- Changed `ref_dir` from `config.project_root / "intodoc" / "reference-docs"` to source-tree-relative path via `Path(__file__).resolve().parent * 5 / "intodoc" / "reference-docs"`.
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines ~382-385) -- Removed Phase 1 MCP calls (`get_scan_status`, `get_chunks`, `fetch_json`) from prompt. Replaced with "All data is pre-processed" instruction.
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines ~579-588) -- Extended PROHIBITED ACTIONS to ban all MCP/fetch tool calls.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~646) -- Changed subprocess cwd from `config.project_root` to source tree path where `.mcp.json` lives.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~284) -- Added warning log when analysis prompts fail to bundle.
- **Problem**: Doc generation ran without Workato-specific analysis prompts (15 prompts + best_practices.yaml), reference doc templates (12 files), or MCP tools. Generated docs lacked platform-specific depth.
- **Root cause**: `.env` sets `MCP_PROJECT_ROOT=/opt/intodata-kb/knowledge_base` which only contains runtime data (`data/`, `intodoc-output/`, `output/`), not source code. All paths resolved from `config.project_root` pointed to non-existent locations. The subprocess cwd also lacked `.mcp.json`.
- **Change**: (1) Prompt lookup uses Python package path (`intodoc.platforms.__file__`), works regardless of project_root. (2) Reference docs use source-tree-relative path from `__file__`. (3) Removed redundant Phase 1 MCP calls from prompt since all data is pre-bundled. (4) Fixed subprocess cwd to source tree. (5) Added warning log for failed prompt bundling.
- **Why this approach**: Package path is the most reliable way to find installed platform files. Source-tree-relative paths avoid config dependency. Removing MCP calls from prompt prevents errors since subprocess has no MCP tools.
- **Verification**: `MCP_PROJECT_ROOT=/opt/... python -c "ScannerService().get_analysis_prompts('workato')"` returns `Count: 23 Error: none`. Reference docs path resolves to correct location with 12 files. MCP server restarted successfully.

### [Feature]: Content-based asset discovery for Workato scanner

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` -- Added `_classify_json_content()` static method (~100 lines), `_build_asset_registry()` method (~25 lines), `_asset_registry` field. Replaced all 12 `rglob()` calls with `self._asset_registry.get()` lookups in `_build_connection_index`, `_build_recipe_id_index`, `_parse_properties`, `_parse_all_recipes`, `_parse_recipe_functions`, `_parse_api_assets`, `_parse_lcap_assets`, `_parse_custom_adapters`, `_parse_schemas`, `_parse_lookup_tables`. Added steps.json sibling merging in `_parse_recipe()`. Added name fallback for generic filenames. Added `property_list` type handling. Added `api_collection` type handling. Updated `_find_workato_root()` with content-based fallback. Unified lookup_table parsing (merged `*.lookup_table.json` and `*.workato_db_table.json` rglob paths into single registry type). Added `from collections import defaultdict` import.
  - `intodoc/src/intodoc/platforms/workato/__init__.py` -- Added `_DIR_NAME_MAP` dict for parent-directory-based fallback classification. Updated `classify_file()` to check parent/grandparent directory names for nested export formats.
  - `intodoc/tests/test_platforms/test_workato.py` -- Added 4 new test classes (51 new tests): `TestClassifyJsonContent` (25 unit tests for content classifier), `TestAssetRegistry` (6 tests on flat + nested fixtures), `TestNestedFormatIntegration` (15 integration tests), `TestDirectoryClassification` (5 tests for dir-based fallback).
  - `intodoc/tests/fixtures/workato_nested/` -- NEW fixture directory (9 files): `manifest.json`, 2 recipe dirs each with `recipe.json` + `steps.json`, 2 connection dirs each with `connection.json`, `lookup_tables/country_codes.json`, `properties/settings.json` (property list format).
- **Problem**: Workato scanner used hardcoded filename patterns (`rglob("*.recipe.json")`, `rglob("*.connection.json")`, etc.) for asset discovery. This only worked for one specific export format. Real Workato exports use a different folder structure (e.g. `recipes/sync_orders/recipe.json` + `recipes/sync_orders/steps.json`) and naming convention, resulting in 0 recipes/connections discovered.
- **Root cause**: The discovery layer was based on 10+ separate `rglob()` calls with suffix patterns like `*.recipe.json`. Files in nested folder format have generic names like `recipe.json` or `connection.json` that don't match these patterns.
- **Change**: Replaced the filename-suffix-based discovery with content-based classification:
  1. `_classify_json_content()` reads parsed JSON and determines asset type from key signatures (15+ types: recipe, connection, lookup_table, custom_adapter, api_endpoint, api_collection, lcap_app, recipe_function, topic, schema, property, property_list, steps, manifest, package). Handles wrapped formats (`{"recipe": {...}}`) by detecting wrapper keys and mapping to types.
  2. `_build_asset_registry()` iterates the file list already produced by `walk_repo()` (no extra filesystem walk), reads each JSON file once, classifies by content, and builds a `dict[str, list[Path]]` registry.
  3. Phase 1b in `parse()` calls `_build_asset_registry()` right after `walk_repo()`.
  4. All 12 `rglob()` calls replaced with `self._asset_registry.get('type', [])` lookups.
  5. Added steps.json sibling merging in `_parse_recipe()` -- if a `steps.json` exists next to a recipe file, its content is merged into the recipe data.
  6. Added name fallback -- when filename is generic (e.g. `recipe.json`), use parent directory name.
  7. Added `property_list` handling for files containing an array of `{name, value}` dicts.
  8. Added `api_collection` handling for files containing multiple endpoints.
  9. Updated `classify_file()` with parent/grandparent directory name fallback for `walk_repo()` stats.
  10. Updated `_find_workato_root()` with content-based recipe detection as final fallback.
- **Why this approach**: Content-based classification is format-agnostic -- it works regardless of file naming or directory structure. The single-pass registry is actually faster than the previous 10+ separate `rglob()` calls. The `walk_repo()` output is reused (no extra filesystem walk). Parsing logic stays identical -- only the discovery layer changes.
- **Verification**: 126/126 tests pass (75 existing unchanged + 51 new). Zero regressions. Tested on 3 fixtures: `workato_sample` (flat format), `workato_new_format` (wrapped format), `workato_nested` (nested folder format).

---

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
