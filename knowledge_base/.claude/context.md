# Current State

**Status**: Phase 2 Complete (Universal IR) + MCP doc generation operational + Dashboard with project management + JWT auth
**Last session**: 2026-03-05
**Last completed**: Unified documentation standards across sub-projects (root logging rules, .claude/ files for KB)

## What's Done

### Scanner CLI (intodoc/)
- Multi-platform architecture (Talend implemented, MuleSoft/dbt designed)
- 22 analysis prompt files (p01-p22) covering all dimensions
- 25 reference docs defining output format/depth
- Universal IR with 99.4% expression parse rate (Phase 2)
- Credential filter (strips secrets before LLM exposure)
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

- **Unified documentation standards** (2026-03-05): Added root-level logging rules, session start requirements, and populated KB .claude/ files (context.md, progress.md, expanded PROJECT_MEMORY.md) to match RAG_pipeline's documentation depth.
- **Fix stuck "generating" status** (2026-03-03): Added polling fallback to SSE-only dashboard updates, reset endpoint for stuck projects, red Reset button in UI.
- **Documentation restructuring** (2026-02-19): Adopted AI Jason's .agent folder pattern, rewrote CLAUDE.md to ~65 lines with pointers, created SOPs and system docs.

## Blockers

- None currently.

## Next Steps

- [ ] Phase 3: Migration code generation (not started)
- [ ] MuleSoft scanner plugin
- [ ] dbt scanner plugin
- [ ] Dashboard: scan history view
- [ ] Dashboard: doc preview/download
- [ ] E2E testing of doc generation pipeline

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
