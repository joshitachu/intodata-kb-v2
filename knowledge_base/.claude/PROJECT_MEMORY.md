# Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| MCP over CLI | Claude Code + MCP replaces CLI-based LLM analyzer | No API keys needed, better quality, parallel sub-agents | 2026-02 |
| Doc generation | 4-phase MCP workflow (Setup, Group Analysis, Write Docs, Publish) | Parallel sub-agents per group for throughput | 2026-02 |
| Staging directory | Claude writes to `docs_staging/`, server swaps to `docs/` on success | Protects existing docs from failed runs | 2026-02 |
| RAG push server-side | `_run_generate_background` calls `RagBridge.push_docs()` | Claude prompt should NOT call `insert_to_rag` -- server handles it | 2026-02 |
| SSE streaming | Real-time log streaming from MCP server to dashboard | Dashboard shows live operation progress | 2026-02 |
| JWT auth | Shared JWT_SECRET_KEY across RAG backend, MCP server, dashboard | Stateless auth, works across all services | 2026-03 |
| Deploy user | Dedicated `intodoc` user on VPS | Security isolation for production deployment | 2026-03 |
| .agent folder pattern | AI Jason pattern: CLAUDE.md (rules) + .agent/ (system, SOPs, decisions, tasks) | CLAUDE.md stays concise (~100 lines), detailed docs are discoverable via README.md index | 2026-02 |
| Dual server mode | `--mcp` (stdio) and `--serve` (HTTP) in same codebase | Share business logic between Claude Code and dashboard | 2026-02 |
| Scanner plugins | `platforms/<name>/` plugin architecture | Each platform self-contained with parser, prompts, best practices | 2026-02 |
| Chunk size | ~30K tokens (~120KB) per chunk | Fits Claude Code context with room for instructions; overhead added on top | 2026-02 |
| Build system | hatchling (NOT setuptools) | pyproject.toml configured for hatchling; setuptools causes build failures | 2026-02 |
| Hindsight memory | Cross-project + project-specific memory via recall/retain tools | Lessons from past runs inform future generation | 2026-03 |
| Polling + SSE | Dashboard uses both SSE log streaming and 10s polling | SSE alone is fragile; polling catches missed state changes | 2026-03 |

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
- Custom hooks: `useProjects()`, `useLogStream()`, `useResetStatus()`
- API client pattern: centralized `client.ts` with typed fetch wrappers
- credentials:"include" on all fetch calls (JWT cookies)
- 401 response -> redirect to login page

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Phase 0: Cleanup | 2026-02 | Repository restructuring, knowledge base |
| Phase 1: Analysis passes | 2026-02 | 21 passes (p01-p21), 32 templates, 25 reference docs |
| Phase 2: Universal IR | 2026-02 | 99.4% expression parse rate |
| MCP server v1 | 2026-02 | 8 MCP tools, REST API, dual mode |
| Dashboard v1 | 2026-02 | Project list, actions, log panel |
| Doc generation pipeline | 2026-02 | 4-phase workflow, parallel sub-agents, staging dir |
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

### Process
- Background sub-agents may return empty output files. Use `TaskOutput` or `resume` to retrieve results. Plan for fallback.
- Doc generation uses staging dir (`docs_staging/`). Server swaps to `docs/` on success, pushes to RAG automatically.
- Do NOT add `insert_to_rag` to the Claude Code prompt -- RAG push is server-side.
- Very large docs (e.g. 17_DATA_QUALITY.md with 1,102 embedded rules) may fail RAG ingestion with 500 errors. Consider splitting.
- SSE-only UI updates are fragile -- always pair with polling as a fallback.
- Stuck states need an explicit user escape hatch (reset button) since disabling all actions locks the user out.

### Build & Tooling
- intodoc uses hatchling build system, NOT setuptools
- Legacy CLI analyzer default $5 budget; can be exceeded with large projects
- tMap distiller must strip noise BEFORE chunking to avoid token bloat
- ENDPOINT/URL parameters are semantic -- never strip them
- Connection matching uses REPOSITORY_PROPERTY_TYPE refs, not name heuristics

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
    api/routes/                     -- REST endpoints (projects, health, logs, webhook)
    services/                       -- Business logic (registry, scanner, RAG bridge, claude)
    db/                             -- SQLAlchemy models (Project, ScanRun, GenerateRun)
  dashboard/src/                    -- React control panel
    components/                     -- ProjectList, ActionButtons, LogPanel, etc.
    hooks/                          -- useProjects, useLogStream, useResetStatus
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
