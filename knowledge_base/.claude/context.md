# Current State

**Status**: Round 2 optimizations COMPLETE. Doc gen target ~29-33 min (was 51.5 min). Needs deploy + test.
**Last session**: 2026-03-11
**Last completed**: Round 2 optimizations: Phase 2.5 analysis condensation (per-agent briefings ~100-150KB instead of 688KB), pre-bundled analysis prompts + reference docs (eliminates MCP dependency), large group splitting (>40 jobs auto-split), rebalanced Phase 3 agents (Core 4/Data+Ops 8/Config+User 13 docs).

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

- [ ] Restart MCP server: `sudo systemctl restart mcp-server`
- [ ] Test doc generation → verify Round 2 optimizations work
- [ ] Check DB tokens: `docker exec postgres psql -U admin -d intodata_kb -c "SELECT id, input_tokens, output_tokens, cost_usd FROM generate_runs ORDER BY id DESC LIMIT 5"`
- [ ] Monitor generation time — target ~29-33 min (down from 51.5 min)
- [ ] Verify Phase 2.5 briefing files are created correctly
- [ ] Verify large group splitting works (USA group → 2 parts)
- [ ] Record baseline benchmark run on VPS (`python -m benchmark run --tag baseline-v1`)
- [ ] Deploy Phase 1 code to VPS (`docker compose up -d --build backend`)
- [ ] Phase 2: Workato scanner (needs sample data from user)
- [ ] MuleSoft scanner plugin
- [ ] dbt scanner plugin
- [ ] E2E testing of doc generation pipeline

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
