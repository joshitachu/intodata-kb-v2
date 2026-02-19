# MCP Server

The MCP server (`mcp-server/`) exposes scan data to Claude Code via the MCP protocol
and provides a REST API for the dashboard.

## Dual Mode

The server runs in two modes via `python -m mcp_server`:

- `--mcp` -- MCP stdio server for Claude Code (configured in `.mcp.json`)
- `--serve` -- REST API on port 8001 for the dashboard

## MCP Tools (8 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `add_repo` | `url`, `project_id`, `platform?` | Register a project (local path, git URL, or 'zip') |
| `fetch_json` | `project_id`, `section?` | Read cached scan JSON by section |
| `trigger_scan` | `project_id`, `force?` | Run `intodoc scan` subprocess |
| `get_scan_status` | `project_id` | Cache age, job count, status |
| `get_chunks` | `project_id` | Chunk definitions (chunk_id, group, job_count, token_estimate) |
| `fetch_chunk` | `project_id`, `chunk_id` | Full job data for one chunk |
| `insert_to_rag` | `project_id`, `docs?` | Push .md files to RAG pipeline |
| `list_projects` | -- | All registered projects with status |

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
| GET | `/mcp/status/{project_id}` | Get project scan status |
| GET | `/mcp/projects` | List all projects |
| GET | `/health` | Health check |
| GET | `/logs/{project_id}/stream` | SSE log streaming |
| POST | `/webhook/scan-complete` | Webhook for scan completion |

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
  config.py            -- Pydantic Settings (paths, ports, URLs)
  schemas.py           -- API request/response models
  mcp/server.py        -- 8 MCP tools + dispatch
  api/
    app.py             -- FastAPI app factory
    routes/
      projects.py      -- Project CRUD + scan/generate/RAG endpoints
      health.py        -- Health check
      logs.py          -- SSE log streaming
      webhook.py       -- Scan completion webhook
  services/
    project_registry.py -- DB operations for projects + scan runs
    scanner_service.py  -- Run intodoc scan, read scan cache/chunks
    rag_bridge.py       -- Push docs to RAG pipeline
    claude_service.py   -- Spawn Claude Code CLI
    log_manager.py      -- Log file management + streaming
  db/
    models.py          -- SQLAlchemy models (Project, ScanRun)
    session.py         -- Async session factory
```

## Adding New Tools/Endpoints

1. Add the tool definition to `mcp/server.py` `list_tools()` return list
2. Add the handler function and register in `_dispatch()`
3. For REST: add the route to `api/routes/projects.py` or a new route file
4. Add request/response schemas to `schemas.py`
