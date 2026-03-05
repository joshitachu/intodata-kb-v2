# intodoc -- Code Intelligence Platform

Multi-project, multi-platform tool that scans integration codebases (Talend, MuleSoft, dbt,
etc.), generates comprehensive documentation, and feeds a RAG knowledge base.

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Scanner CLI | `intodoc/` | Parses source code into structured scan cache JSON |
| MCP Server | `mcp-server/` | Exposes scan data to Claude Code via MCP tools + REST API |
| Dashboard | `dashboard/` | React control panel for managing projects, scans, docs |
| RAG Pipeline | `../RAG_pipeline/` | FastAPI + Qdrant for document search |

## Quick Commands

```bash
# MCP server (stdio, for Claude Code)
cd mcp-server && python -m mcp_server --mcp

# REST API (port 8001, for dashboard)
cd mcp-server && python -m mcp_server --serve

# Dashboard (port 5173)
cd dashboard && npm run dev

# Run a scan
cd intodoc && intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output

# RAG pipeline (port 8000)
cd ../RAG_pipeline/backend && python -m uvicorn src.api.main:app --port 8000
```

## Rules

1. **Read `.agent/README.md` first** -- it tells you which docs to read for your task
2. **Read `.claude/` files before starting work** -- read `context.md`, `PROJECT_MEMORY.md`, `progress.md`, and `PROGRESS_LOG.md` at session start. Follow the unified logging rules in root `CLAUDE.md` (section "Logging Rules") for all changes -- same format, same discipline as `RAG_pipeline/`.
3. **Credential filter first** -- never send raw credentials to any LLM
4. **ASCII-only Markdown** -- no Unicode box-drawing characters in generated docs
5. **No intermediate files** -- only final `.md` docs in `output/{project_id}/docs/` (generated via `docs_staging/`, see Documentation Generation)
6. **Never hardcode group names** -- use whatever `get_chunks()` returns
7. **Never call `trigger_scan()`** unless cache is explicitly stale or missing

## Documentation Generation

The MCP-based 4-phase documentation workflow is documented in:
**`.agent/system/mcp-workflow.md`**

This is the primary method for generating project documentation. Follow it exactly.

### Safe Re-Runs (Staging Directory)

Doc generation uses a **staging directory** (`docs_staging/`) to protect existing docs:

1. Claude Code writes new docs to `output/{project_id}/docs_staging/`
2. On **success**: old `docs/` is removed, `docs_staging/` is renamed to `docs/`, then server pushes to RAG automatically
3. On **failure**: `docs_staging/` is deleted, old `docs/` remains intact, `doc_count` unchanged

Key files: `claude_service.py` (prompt + staging dir setup), `projects.py:_run_generate_background` (swap logic + server-side RAG push).

**Do NOT** add `insert_to_rag` back to the Claude Code prompt -- RAG push is handled server-side in `_run_generate_background` via `RagBridge.push_docs()`.

## Code Conventions

- **Python**: 3.11+, Pydantic v2, Click CLI, hatchling build system
- **Frontend**: React 19, Vite 6, TypeScript 5, Tailwind CSS 3
- **Tests**: pytest (Python), Vitest (frontend)

## Known Pitfalls

- **Windows encoding**: cp1252 breaks Unicode chars -- use ASCII in all output
- **Token bloat**: tMap distiller must strip noise BEFORE chunking
- **ENDPOINT preservation**: don't strip URL/REST params -- they are semantic
- **Connection matching**: use `REPOSITORY_PROPERTY_TYPE` refs, not name matching
- **hatchling**: use hatchling, NOT setuptools -- pyproject.toml is configured for it
- **Budget**: legacy CLI analyzer default $5 limit; 18 passes on 9 chunks can exceed

## Project Structure

```
vaxxinova/                          -- Platform root
  .agent/                           -- Agent context docs (README, system, SOPs, tasks)
  .claude/                          -- Claude Code settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- This file
  mcp-server/                       -- MCP + REST API server (Python, FastAPI)
  dashboard/                        -- React control panel (Vite + Tailwind, :5173)
  intodoc/                          -- Scanner CLI (see intodoc/CLAUDE.md)
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```
