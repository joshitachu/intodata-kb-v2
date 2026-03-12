# Progress Log

Session log for the intodoc platform. Every session should add an entry documenting
what was done, what changed, and lessons learned.

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
