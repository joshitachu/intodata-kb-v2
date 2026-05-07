# Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| MCP over CLI | Claude Code + MCP replaces CLI-based LLM analyzer | No API keys needed, better quality, parallel sub-agents | 2026-02 |
| Doc generation | 4-phase MCP workflow (Setup, Group Analysis, Write Docs, Publish) | Parallel sub-agents per group for throughput | 2026-02 |
| Staging directory | Claude writes to `docs_staging/`, server swaps to `docs/` on success | Protects existing docs from failed runs | 2026-02 |
| Resumable generation | On failure, staging is preserved; on resume, `_detect_resume_state()` inspects artifacts and `_manifest.json` tracks scan mtime for stale detection | Prevents loss of ~30 min work on rate limit failures | 2026-04 |
| RAG push server-side | `_run_generate_background` calls `RagBridge.push_docs()` | Claude prompt should NOT call `insert_to_rag` -- server handles it | 2026-02 |
| SSE streaming | Real-time log streaming from MCP server to dashboard | Dashboard shows live operation progress | 2026-02 |
| JWT auth | Shared JWT_SECRET_KEY across RAG backend, MCP server, dashboard | Stateless auth, works across all services | 2026-03 |
| Webhook auth | GitHub webhook uses HMAC-SHA256 (`X-GitHub-Signature-256`), NOT JWT | GitHub cannot send cookies; webhook router is registered without `require_auth` | 2026-04 |
| Analysis cache | After every successful generation, `docs/_analysis/` is copied to `output/{project_id}/analysis_cache/` | Enables incremental webhook regen: unchanged groups skip Phase 2 by restoring cached summaries into `docs_staging/_analysis/` before generation with `resume=True` | 2026-04 |
| Deploy user | Dedicated `intodoc` user on VPS | Security isolation for production deployment | 2026-03 |
| .agent folder pattern | AI Jason pattern: CLAUDE.md (rules) + .agent/ (system, SOPs, decisions, tasks) | CLAUDE.md stays concise (~100 lines), detailed docs are discoverable via README.md index | 2026-02 |
| Dual server mode | `--mcp` (stdio) and `--serve` (HTTP) in same codebase | Share business logic between Claude Code and dashboard | 2026-02 |
| Scanner plugins | `platforms/<name>/` plugin architecture | Each platform self-contained with parser, prompts, best practices | 2026-02 |
| Chunk size | ~30K tokens (~120KB) per chunk | Fits Claude Code context with room for instructions; overhead added on top | 2026-02 |
| Build system | hatchling (NOT setuptools) | pyproject.toml configured for hatchling; setuptools causes build failures | 2026-02 |
| Hindsight memory | Cross-project + project-specific memory via recall/retain tools | Lessons from past runs inform future generation | 2026-03 |
| Polling + SSE | Dashboard uses both SSE log streaming and 10s polling | SSE alone is fragile; polling catches missed state changes | 2026-03 |
| Content-based discovery | Workato scanner uses JSON content classification instead of filename patterns | Works with any export format (flat suffix, wrapped, nested folder); single pass reuses walk_repo() output | 2026-03 |
| Workato generic extraction | All field reads use `or` fallback chains (e.g. `provider or application`); param extractors iterate all keys instead of hardcoded lists | Handles 3 known export formats (A=code blocks, B=flat, C=manifest/package) plus unknown future variations | 2026-03 |
| Bundle splitting | Prompt bundles split at 200KB, ref doc bundles at 80KB; glob patterns in orchestrator prompt | Prevents exceeding Claude's 256KB file-read limit and 25K token limit as platforms add more prompts | 2026-03 |
| Consolidated analysis guide | Single ANALYSIS_GUIDE.md per platform replaces 22 bundled .txt prompts; PHASE_3_GUIDE.md for cross-group synthesis | 94% token reduction (222KB->12KB), eliminates conflicting instructions, coherent single-document format; original .txt files kept for backward compat | 2026-03 |
| Transformation detail depth | Platform-aware preprocessing renders tmap_data/filter chains as structured tables; TRANSFORMATION_DETAIL section in analysis guides; per-job transformation subsections mandatory in Phase 3 | Closes the field-level documentation gap: users can now ask "what does column X do in job Y?" and get an answer from docs alone | 2026-04 |
| Dynamic depth scaling | Inject project metrics (total_jobs, total_groups) into prompt instead of hard-coded line minimums | Small projects don't pad, large projects don't truncate; depth driven by content not arbitrary targets | 2026-03 |
| Template DB storage | Templates, versions, profiles in PostgreSQL with Alembic migrations | Enables admin visibility, versioning with commit messages, A/B testing via profiles | 2026-03 |
| Template resolution cascade | Explicit profile -> project-scoped default -> platform default -> filesystem fallback | Flexible selection with permanent backward compat; filesystem always works as safety net | 2026-03 |
| Full-copy template variants | Profiles map template slots to specific versions (full copies, not section overrides) | Simpler to reason about; each version is self-contained and independently testable | 2026-03 |
| Per-platform template sets | Templates have optional `platform` field; profiles scoped to platform | Talend and Workato templates are independent; prevents cross-contamination | 2026-03 |
| Phase 2.6 scoring | New scoring sub-agent between Phase 2.5 gate and Phase 3 doc writing | Quantitative grounding for docs; enables comparison across runs; scorecard included in final output | 2026-03 |
| Template snapshots on runs | `template_snapshot` JSONB on GenerateRun records `{slug: version_id}` | Exact reproducibility; enables meaningful A/B comparison between runs using different profiles | 2026-03 |
| Phase 2.8 analysis bundling | Deterministic Python script bundles analysis per Phase 3 agent; replaces agent-side grep/head/tail | Large projects (>400KB analysis) can't fit in 200K context; server-side section selection is reliable, agent-side grep is not | 2026-04 |
| Depth tier scaling | small (<50 jobs, 0.6x), medium (50-199, 1.0x), large (200+, 1.5x) minimum line targets | Large projects need proportionally deeper docs; prevents the paradox of more jobs producing less content | 2026-04 |
| 4-agent Phase 3 split | Projects >=200 jobs use 4 agents (Core, Business+Ops, Patterns, User) instead of 3 | Fewer docs per agent = more output headroom per doc; Patterns+User was overloaded with 5 docs at large scale | 2026-04 |
| Custom provider investigator loop | API-model custom harness now runs a Python-owned JSON action loop before Phase 2 | Gets Claude-Code-like investigation without Claude CLI or LangChain in production path; model can request read/search/list, but Python controls permissions, validation, artifact writes, and phase gates | 2026-04 |
| Two-provider doc gen design | Only `claude-cli` and `custom harness` providers; orchestrator.py + tools.py deleted | CLI prompt IS orchestration; harness uses per-call focused prompts. They share the same `ANALYSIS_GUIDE.md` content (not the same prompts). `DocGenContext` holds only shared fields; cli-only computations live in `_build_prompt()`. | 2026-04 |
| Analysis guide not stripped for harness | `_adapt_legacy_text()` removed from `prompt_packs.py`; guide content passes through unmodified | The guide is about semantic extraction targets, not orchestration — stripping it degraded harness output quality silently. Full guide now reaches harness model calls via `_analysis_prompt_pack.md`. | 2026-04 |
| Config-driven generation constants | All bundle limits, QS thresholds, token caps, and RAG timeouts are Pydantic Settings fields (MCP_ prefix) | Eliminates magic numbers scattered across 4 files; operator can tune via env vars without code changes | 2026-04 |
| RAG push filter: top-level NN_*.md only | `_read_md_files` uses `glob("*.md")` + digit-prefix filter, not `rglob("**/*.md")` | `_assessment/`, `_analysis/`, `_investigation/` subdirs contain system prompts and debug output that must not appear in user query results | 2026-04 |
| Doc quality scorer scales with unit_count | `_scaled_min_lines(doc_key, unit_count)` applies sqrt(unit_count/50) clamped [0.3x, 2.0x] | Static thresholds calibrated for 500-job Talend project incorrectly graded 17-recipe Workato project as failing; scoring must be relative to project size | 2026-04 |
| phase3_max_tokens = 32000 | deepseek-v4-pro (and kimi-k2.6) support 32K output tokens; config default raised from 10000 | Old 10000 cap truncated docs mid-sentence — root cause of quality score 2.38/5.0 on plsss | 2026-04 |
| context_max_tokens pre-flight guard | Estimate tokens before every API call; raise at 900K, warn at 85% | Prevents silent context overflows that waste the full API cost and produce no output; configurable via MCP_CONTEXT_MAX_TOKENS | 2026-04 |
| DATA_FLOWS CSV-only (no JSON base) | 03_DATA_FLOWS_LINEAGE overrides base names list to CSV versions only | entity_index + usage_index were loaded twice (JSON base + CSV extra = ~925 KB duplicate); CSVs are more readable for LLMs and cover the same data | 2026-04 |
| Phase B: native tool-use loop | list_artifacts/read_artifact tools on OpenAICompatibleProvider + AnthropicProvider; Ollama keeps single-shot fallback; supports_tools flag routes call sites in harness.py | Model pulls context on demand instead of Python front-loading; recovers from non-standard filenames; context_bytes=0 for tool-use paths eliminates context overflow risk | 2026-04 |
| DeepSeek multi-turn reasoning_content | complete_with_tools() echoes reasoning_content back in assistant messages when present | DeepSeek thinking mode returns reasoning_content in every response; multi-turn loops must include it in history or API returns 400. Single-shot complete() unaffected. No-op for non-thinking models. | 2026-04 |
| Platform-agnostic provider requirement | No Claude CLI dependency; all features must work with deepseek/kimi/Anthropic/Ollama swappable at runtime | Anthropic partnership (token resale) requires Claude support but architecture must not be Claude-only; tool-use implementation uses OpenAI function-calling format which all three cloud providers support | 2026-04 |

## Code Patterns

### Python (Scanner + MCP Server)
- Python 3.11+, type hints everywhere
- Pydantic v2 for data models and settings
- Click for CLI entry points
- async/await for I/O (FastAPI routes, DB queries)
- Never bare except -- always catch specific exceptions
- hatchling build system (NOT setuptools)

### Dashboard (React)
- React 19 + Vite 6 + TypeScript 5 strict
- Tailwind CSS 3 for styling
- Custom hooks: `useProjects()`, `useLogStream()`, `useResetStatus()`, `useTemplates()`, `useProfiles()`
- API client pattern: centralized `client.ts` with typed fetch wrappers
- credentials:"include" on all fetch calls (JWT cookies)
- 401 response -> redirect to login page
- Top-level nav: Projects / Templates / Profiles / Docs
- Template editor: inline textarea with version selector + required commit messages
- Profile dropdown on Generate button for template swapping

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Phase 0: Cleanup | 2026-02 | Repository restructuring, knowledge base |
| Phase 1: Analysis passes | 2026-02 | 21 passes (p01-p21), 32 templates, 25 reference docs |
| Phase 2: Universal IR | 2026-02 | 99.4% expression parse rate |
| MCP server v1 | 2026-02 | 8 MCP tools, REST API, dual mode |
| Dashboard v1 | 2026-02 | Project list, actions, log panel |
| Doc generation pipeline | 2026-02 | 4-phase workflow, parallel sub-agents, staging dir |
| Template management system | 2026-03-24 | DB-backed templates, versioning, profiles, A/B comparison, dashboard UI |
| Scoring pipeline (Phase 2.6) | 2026-03-24 | 8-dimension rubric, anti-patterns, red flags, security checklist, scorecard output |
| Custom investigator loop | 2026-04-25 | `providers/custom/investigator.py`; JSON actions (`read_file`, `search_text`, `list_dir`, `finish`), validated `_investigation/` artifacts, Phase 2/3 evidence threading, critic/expander pass |
| Documentation restructuring | 2026-02-19 | .agent pattern, SOPs, system docs |
| Fix stuck status | 2026-03-03 | Reset endpoint, polling fallback, reset button |
| JWT auth integration | 2026-03-04 | Shared JWT across all services |
| Doc gen run: TESTClAUDE | 2026-02-20 | 25 docs generated, 24/25 pushed to RAG |

## Lessons Learned / Gotchas

### Formatting
- Windows cp1252 encoding breaks Unicode -- always use ASCII in all output (chars 32-126)
- No em-dashes, smart quotes, or Unicode arrows -- use `--`, `'`, `->` instead
- All tables must use plain ASCII pipe characters

### Content
- Never hardcode group names -- use `get_chunks()` return values
- Never assume specific region names -- they vary per project
- Every document MUST have a table of contents
- Business rules tagged as BR-{GROUP}-NNN
- Quantify everything: "75 jobs" not "many jobs"
- Do NOT invent data -- every claim must trace to a group summary or manifest
- **Per-job purpose lines live in `02_ARCHITECTURE_INVENTORY.md` as `#### {JobName}` followed by a `**Purpose**: ...` paragraph.** Do NOT substring-grep paragraphs for the job name -- the Purpose paragraph does NOT repeat the job name, so a naive grep matches the heading itself (too short, gets discarded) or an unrelated deeper section. Always parse the heading boundary and take the next paragraph that starts with `**Purpose**`. See `_extract_job_purpose_from_inventory` in `routes/projects.py`.
- The Talend per-job JSON `purpose` field from the scanner is always empty -- the only source of real, brief job descriptions is the markdown `**Purpose**:` line written by Claude during doc generation.

### Process
- Background sub-agents may return empty output files. Use `TaskOutput` or `resume` to retrieve results. Plan for fallback.
- Doc generation uses staging dir (`docs_staging/`). Server swaps to `docs/` on success, pushes to RAG automatically.
- Do NOT add `insert_to_rag` to the Claude Code prompt -- RAG push is server-side.
- Custom API provider has its own investigator loop now. The model never writes files directly in that phase. It returns JSON actions only; Python executes read-only tools inside `docs_staging/`, validates evidence, and writes `_investigation/{group}.json` + `.md`. If investigation coverage fails, Phase 2 must not start.
- Very large docs (e.g. 17_DATA_QUALITY.md with 1,102 embedded rules) may fail RAG ingestion with 500 errors. Consider splitting.
- SSE-only UI updates are fragile -- always pair with polling as a fallback.
- Stuck states need an explicit user escape hatch (reset button) since disabling all actions locks the user out.

### Custom Provider Testing
- Focused regression command for the custom provider:
  ```bash
  cd knowledge_base/mcp-server
  PYTHONDONTWRITEBYTECODE=1 pytest tests/test_providers/test_custom_provider.py
  ```
- Lint command for the touched custom provider code:
  ```bash
  cd knowledge_base/mcp-server
  ruff check src/mcp_server/providers/custom tests/test_providers/test_custom_provider.py
  ```
- Runtime smoke test for the investigator feature:
  1. Configure `MCP_DOC_GENERATION_PROVIDER=custom` and a real API model (`moonshot/kimi-k2.6`, `openai/<model>`, or another supported `provider/model` string).
  2. Generate docs for a small scanned project.
  3. Watch logs for `[custom] Investigation complete` and `[custom] Investigation coverage gate passed`.
  4. During staging, inspect `_investigation/*.json` and `_investigation/*.md` for exact canonical unit names, source files, snippets, and categories.
  5. After success, confirm `provider_calls.json` includes phase `investigation` entries. `_preprocess/` and `_analysis/` are cleaned; `_investigation/` is kept as audit evidence.
- Full `pytest` in `mcp-server` currently has an unrelated SQLite fixture blocker: PostgreSQL `JSONB` column `generate_runs.template_snapshot` cannot compile under SQLite. The provider test file passes independently.

### Scanner
- Workato file discovery uses content-based classification (`_classify_json_content`), NOT filename patterns
- The asset registry is built once from `walk_repo()` output -- no extra filesystem walk, no rglob
- Three Workato export formats exist: flat suffix (`.recipe.json`), wrapped (`{"recipe": {...}}`), nested folder (`recipes/name/recipe.json` + `steps.json`)
- Steps can live in a sibling `steps.json` file -- `_parse_recipe()` auto-merges them
- When recipe filename is generic (e.g. `recipe.json`), use parent directory name as recipe name

### Build & Tooling
- intodoc uses hatchling build system, NOT setuptools
- Legacy CLI analyzer default $5 budget; can be exceeded with large projects
- tMap distiller must strip noise BEFORE chunking to avoid token bloat
- ENDPOINT/URL parameters are semantic -- never strip them
- Connection matching uses REPOSITORY_PROPERTY_TYPE refs, not name heuristics

### Deployment / Systemd

- **EP-001 canonical deployment path is nginx/certbot, not Caddy.** As of 2026-05-05, EP-001 is generic Deployment Foundation. Use `deploy/deploy-client.sh`, `deploy/nginx/nexus-client.conf.template`, `deploy/mcp-server@.service`, `deploy/RUNBOOK.md`, and `deploy/SMOKE-TEST.md` for new deployment work. Caddy files remain legacy/deferred and should not be used as EP-001 acceptance unless a future story explicitly revives them.
- **Six Docker services plus host MCP API are the deployment health boundary.** Docker Compose covers `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, and `dashboard`. MCP API is required but runs via systemd (`mcp-server@<client>.service`), so runbooks and smoke tests must verify both `docker compose ps` and `/mcp/health`.
- **EP-001 is not accepted by docs alone.** Backlog/docs are aligned, but final acceptance requires executing `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md` on a clean reference VPS, including HTTPS/security headers, login/logout, ingest, streaming query, dashboard, Ollama container access, and IP allowlisting.

- **Editing `deploy/mcp-server.service` in the repo does NOT update the installed unit.** The systemd unit at `/etc/systemd/system/mcp-server.service` must be re-copied + `systemctl daemon-reload` + `systemctl restart mcp-server` whenever the repo file changes (working directory, venv path, env file, etc.). On 2026-04-10 this caused the v2 checkout's MCP server to keep running stale code from a Mar-24 process for 2+ weeks; the topology endpoint `/mcp/projects/{id}/graph` returned FastAPI's default 404 because the route did not exist in the in-memory binary.
- **After adding a new Python dependency to `mcp-server/pyproject.toml`, you MUST `pip install`** it into the venv that systemd uses. Editable installs do NOT auto-install new deps -- they only re-link source files. The `ollama` package was added to v2 deps but never installed, crash-looping the service with `ModuleNotFoundError: No module named 'ollama'` after the unit was finally re-installed.
- **Canonical data location is now under `intodata-kb-v2/`**: `intodoc-output/` and `output/` live at `/home/intodata/intodata-kb-v2/knowledge_base/{intodoc-output,output}`. The old paths under `/home/intodata/intodata-kb/knowledge_base/` are now symlinks pointing at v2 as a temporary safety net.

### Topology graph endpoint

- **`/projects/{id}/graph` synthesizes `job_connections_used` / `connection_used_by` at request time when the manifest is missing `job_external_systems`.** This is the legacy-scan fallback path in `_derive_systems_from_job_json` -> `_fallback_talend_system` (`mcp-server/src/mcp_server/api/routes/projects.py`). Modern scans populate `job_external_systems` explicitly and bypass this path entirely. The Talend heuristic strips a leading `t` and trailing `Input/Output/Connection/...` suffixes from the component type to guess a system name -- so it MUST exclude every internal Talend component type that carries non-system payloads, otherwise it produces hub-shaped fake-system noise that wrecks topology rendering performance for legacy scans. Internal components to exclude: tMap, tRunJob, tPrejob, tPostjob, tWarn, tDie, tLog* (tLogRow, tLogCatcher), tStatCatcher, tFlowMeter, tFlowMeterCatcher, tChronometerStart, tChronometerStop, tAssert, tAssertCatcher, tJava*, tJavaRow, tJavaFlex, tFixedFlowInput, tFlowToIterate, tSetGlobalVar, tContextLoad, tRoute*, tUnite, tReplicate, tSortRow, tFilterRow, tAggregateRow, tFile* (Input/Output Delimited/Excel, List, Delete, Copy, Exist), tWriteJSONField, tExtract*Field, tSchemaComplianceCheck, tSleep, tLoop, tMsgBox, tBufferInput, tBufferOutput, tNormalize, tDenormalize, tIterateToFlow.
- **The `tRunJob` family wraps internal job-call wiring whose payload looks like `REFERENCE:sendToMonitoringMOM`, `REFERENCE:Trigger_Next_Interface_92`, etc.** When this leaks through the suffix-stripping path it becomes a synthetic "REFERENCE:..." system that fans out across the entire view (one per called job). Always filter `system.startswith("REFERENCE")` defensively in the Talend branch of `_derive_systems_from_job_json` AND keep the tRunJob family in `_FALLBACK_INTERNAL_COMPONENTS`. Cap the fallback's per-job result at 6 systems (`sorted(systems)[:6]`) -- modern scans bypass this so the cap only floors heuristic noise on legacy scans.
- **Symptoms when this gets out of hand**: scherpenhuizen-talend (legacy scan) was returning ~37 connection nodes and ~256 job-conn edges from a manifest that on disk had zero connection data; the resulting hub-shaped graph (Oracle 29 jobs, REFERENCE:sendToMonitoringMOM 27 jobs) made the topology view stutter on hover and on every direction/compact toggle. Round 3 of the topology perf pass (2026-04-10) cleaned this up.

### RBAC is duplicated across MCP server and RAG backend

- **The MCP server and the RAG backend each have their own RBAC code path against the same PostgreSQL tables (`users`, `user_roles`, `role_permissions`, `permissions`, `project_assignments`).** RAG backend uses SQLAlchemy via `get_user_project_ids()` in `RAG_pipeline/backend/src/rbac/permissions.py`; MCP server uses raw-SQL helpers in `mcp-server/src/mcp_server/api/routes/projects.py` (`_get_user_is_superadmin`, `_get_user_permission_codenames`, `_get_assigned_project_ids`) plus `require_project_access()` in `mcp-server/src/mcp_server/api/permissions.py`. **Any change to the permission model (new codename, new role, change to assignment semantics) must be made in BOTH services or the two surfaces will disagree.**
- **Routes that need RBAC on the MCP side are easy to miss.** Router-level `dependencies=[Depends(require_auth)]` only guarantees the user is authenticated, not that they can see the `project_id`. Every new route under `/projects/{project_id}/...` must either call `await require_project_access(request, project_id)` explicitly, or filter its output by user assignment. On 2026-04-20 three routes were found missing this: `/deep-dive/projects` returned every project, and `/projects/{id}/graph` + `/projects/{id}/jobs/{job_name}/detail` accepted any project_id from any authenticated user. Fixed by mirroring the `list_projects()` pattern for the list route and adding `require_project_access` to the two detail routes.

## Environment

- Python: 3.11+ (pyproject.toml), 3.14 on some dev machines
- Node: v24+ (dashboard)
- Build: hatchling (Python), Vite 6 (dashboard)
- Deployment: VPS with `intodoc` user, systemd services, nginx reverse proxy
- Ports: 8001 (MCP REST API), 5173 (dashboard dev)
- Docker: dashboard served via nginx in docker-compose

## File Structure Summary

```
knowledge_base/
  .agent/                           -- Agent context docs
    README.md                       -- Smart index ("when to read what")
    system/                         -- architecture.md, mcp-workflow.md, mcp-server.md, etc.
    sop/                            -- run-doc-generation.md, add-new-project.md, etc.
    decisions/                      -- 001-mcp-over-cli.md
    tasks/                          -- Phase FRDs
  .claude/                          -- Session tracking
    context.md                      -- Current state, blockers, next steps
    progress.md                     -- High-level phase tracking
    PROGRESS_LOG.md                 -- Detailed session log
    PROJECT_MEMORY.md               -- This file
  CLAUDE.md                         -- Platform rules (concise, ~65 lines)
  .mcp.json                         -- MCP server config for Claude Code
  mcp-server/src/mcp_server/        -- MCP + REST server
    mcp/server.py                   -- 8 MCP tools
    api/routes/                     -- REST endpoints (projects, health, logs, webhook, templates, generate_runs)
    services/                       -- Business logic (registry, scanner, RAG bridge, claude, template_service, template_resolver)
    db/                             -- SQLAlchemy models (Project, ScanRun, GenerateRun, Template, TemplateVersion, TemplateProfile, ProfileAssignment)
    templates/                      -- Scoring templates (rubric, anti-patterns, red-flags, security, patterns)
    scripts/seed_templates.py       -- DB seeder for filesystem templates
    alembic/                        -- Database migrations
  dashboard/src/                    -- React control panel
    components/                     -- ProjectList, ActionButtons, LogPanel, etc.
    components/templates/           -- TemplateCatalogue, TemplateEditor, VersionTimeline, ProfileManager, ProfileDetail, ABComparison, DocumentCatalogue
    hooks/                          -- useProjects, useLogStream, useResetStatus, useTemplates
    api/client.ts                   -- REST API client
  intodoc/src/intodoc/              -- Scanner CLI
    cli.py                          -- Click entry point
    models.py                       -- 30+ Pydantic models
    scanner/                        -- File walker, credential filter
    platforms/talend/               -- Talend parser, prompts, best practices
    chunker/                        -- Region-based chunking
    ir/                             -- Universal IR
  intodoc-output/                   -- Scan cache (read-only during doc gen)
  output/                           -- Generated docs + logs
```

## How to Resume Development

1. Read `context.md` for current state
2. Read `PROJECT_MEMORY.md` (this file) for architecture and patterns
3. Read `.agent/README.md` for the "when to read what" index
4. Check `PROGRESS_LOG.md` for what was done in previous sessions
5. For doc generation work, read `.agent/system/mcp-workflow.md`
6. For MCP server work, read `.agent/system/mcp-server.md`
7. Run dashboard: `cd dashboard && npm run dev`
8. Run MCP server: `cd mcp-server && python -m mcp_server --serve`
