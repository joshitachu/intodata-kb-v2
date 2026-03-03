# Progress Log

Session log for the intodoc platform. Every session should add an entry documenting
what was done, what changed, and lessons learned.

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
