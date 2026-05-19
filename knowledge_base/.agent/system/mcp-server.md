# MCP Server

The MCP server (`mcp-server/`) exposes scan data to Claude Code via the MCP protocol
and provides a REST API for the dashboard.

## Dual Mode

The server runs in two modes via `python -m mcp_server`:

- `--mcp` -- MCP stdio server for Claude Code (configured in `.mcp.json`)
- `--serve` -- REST API on port 8001 for the dashboard

## MCP Tools (14 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `add_repo` | `url`, `project_id`, `platform?` | Register a project (local path, git URL, or 'zip') |
| `fetch_json` | `project_id`, `section?` | Read cached scan JSON by section |
| `trigger_scan` | `project_id`, `force?` | Run `intodoc scan` subprocess — only if cache is stale/missing |
| `get_scan_status` | `project_id` | Cache age, job count, status |
| `get_chunks` | `project_id` | Chunk definitions (chunk_id, group, job_count, token_estimate) |
| `fetch_chunk` | `project_id`, `chunk_id` | Full job data for one chunk |
| `insert_to_rag` | `project_id`, `docs?` | Push .md files to RAG — do not call during doc generation; push is server-side via `RagBridge.push_docs()` |
| `list_projects` | -- | All registered projects with status |
| `get_analysis_prompts` | `project_id`, `platform?` | Returns bundled `ANALYSIS_GUIDE.md` and `PHASE_3_GUIDE.md` content |
| `get_reference_docs` | -- | Returns reference doc content for output format/structure |
| `recall_process_memory` | `query` | Query Hindsight for cross-project process lessons |
| `recall_project_memory` | `project_id`, `query` | Query Hindsight for project-specific findings |
| `retain_findings` | `project_id`, `content` | Store project findings in Hindsight |
| `retain_process_lesson` | `content` | Store cross-project lesson in Hindsight |

### fetch_json Sections

- `meta` -- platform, repo_root, stats, scanned_at
- `manifest` -- job_calls, job_called_by, connections_used_by, routines_used_by
- `connections` -- all metadata connections
- `contexts` -- context variable groups
- `jobs/{GROUP}` -- all jobs in a specific group
- `all` -- meta + manifest + connections + job group summary (not full job data)

## REST API Endpoints

All endpoints are under `/mcp/` prefix:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/mcp/add-repo` | Register a project |
| POST | `/mcp/upload-zip` | Upload ZIP, unpack, register |
| POST | `/mcp/scan` | Trigger scan (background task) |
| POST | `/mcp/generate` | Spawn Claude Code CLI for doc generation |
| POST | `/mcp/push-to-rag` | Push docs to RAG pipeline |
| POST | `/mcp/reset-status` | Reset stuck generating/scanning project to error |
| GET | `/mcp/status/{project_id}` | Get project scan status |
| GET | `/mcp/projects` | List all projects |
| GET | `/health` | Health check |
| GET | `/logs/stream?project_id=...` | SSE log streaming |
| POST | `/webhook/github/webhook` | GitHub webhook (HMAC-verified, bypasses JWT) |

## Configuration (.mcp.json)

```json
{
  "mcpServers": {
    "intodoc": {
      "command": "python",
      "args": ["-m", "mcp_server", "--mcp"],
      "cwd": "<platform-root>/mcp-server"
    }
  }
}
```

## Source Structure

```
mcp-server/src/mcp_server/
  __main__.py          -- Entry point (--mcp or --serve)
  config.py            -- Pydantic Settings (paths, ports, URLs, API keys)
  schemas.py           -- API request/response models
  mcp/server.py        -- 14 MCP tools + dispatch
  api/
    app.py             -- FastAPI app factory (JWT auth middleware on /mcp/*)
    routes/
      projects.py      -- Project CRUD + scan/generate/RAG/GitHub webhook endpoints
      health.py        -- Health check
      logs.py          -- SSE log streaming (/logs/stream?project_id=...)
      webhook.py       -- GitHub webhook (HMAC, no JWT)
  services/            -- 25+ service files including:
    project_registry.py -- DB operations for projects + scan/generate runs
    scanner_service.py  -- Run intodoc scan, read scan cache/chunks
    rag_bridge.py       -- Push docs to RAG pipeline
    claude_service.py   -- Route doc generation to active provider
    log_manager.py      -- Log file management + streaming
    assessment_service.py, deep_dive_service.py, doc_quality_scorer.py
    auto_doc_update.py, git_poller.py, git_reingestion.py
    pdf_service.py, plugin_runner.py, summary_service.py
    model_pricing.py, ops_telemetry.py, validation_service.py
  providers/           -- Doc generation backends
    base.py            -- Abstract provider interface
    claude_cli/        -- Spawns Claude Code CLI subprocess
    custom/            -- API-based harness (agent_runtime.py, harness.py)
  db/
    models.py          -- SQLAlchemy models (Project, ScanRun, GenerateRun, PluginPack)
    session.py         -- Async session factory
```

Note: all `/mcp/*` routes require JWT cookie auth. The GitHub webhook route explicitly bypasses JWT and uses HMAC verification instead.

## Adding New Tools/Endpoints

1. Add the tool definition to `mcp/server.py` `list_tools()` return list
2. Add the handler function and register in `_dispatch()`
3. For REST: add the route to `api/routes/projects.py` or a new route file
4. Add request/response schemas to `schemas.py`
