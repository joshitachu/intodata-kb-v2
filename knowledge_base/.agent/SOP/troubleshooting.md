# Troubleshooting Guide

Common issues and fixes for the intodoc platform.

## Scanner Issues

### Windows encoding (cp1252 vs UTF-8)
**Symptom**: `UnicodeEncodeError` or garbled characters in output.
**Fix**: Use ASCII-only text in all CLI output and generated documents.
Avoid em-dashes, arrows, and other Unicode characters.

### Token bloat in scan data
**Symptom**: Chunks exceed context window limits; analysis is truncated.
**Fix**: The tMap distiller must strip noise BEFORE chunking. Ensure
`field_type` filtering is active in the distiller pipeline.

### ENDPOINT preservation
**Symptom**: REST/OData API endpoints missing from scan data.
**Fix**: Don't strip URL-related parameters in the distiller. Endpoints
like REST URLs are semantically important and must be preserved.

### Connection matching failures
**Symptom**: Jobs don't link to their database connections.
**Fix**: Use component `REPOSITORY_PROPERTY_TYPE` references for matching,
NOT region-name heuristics.

### hatchling build errors
**Symptom**: `pip install -e .` fails with setuptools errors.
**Fix**: intodoc uses hatchling, not setuptools. Ensure `pyproject.toml`
has `build-system.build-backend = "hatchling.build"`.

### Budget exceeded (legacy CLI)
**Symptom**: Analyzer stops mid-run with budget error.
**Fix**: Default limit is $5. Increase with `--budget` flag. 18 passes
on 9 chunks can exceed $5 with larger models.

## MCP Server Issues

### MCP server not connecting
**Symptom**: Claude Code can't find intodoc MCP tools.
**Fix**: Verify `.mcp.json` exists in the working directory and points to the
correct `mcp-server/` path. Restart Claude Code to reload MCP config.

### "Project not found" errors
**Symptom**: MCP tools return `{"error": "Project not found: ..."}`.
**Fix**: Register the project first with `add_repo()`. Check `list_projects()`
to see what's registered.

### Scan fails with "intodoc command not found"
**Symptom**: `trigger_scan()` fails because intodoc CLI is not installed.
**Fix**: Install the scanner: `cd intodoc && pip install -e .`

## Dashboard Issues

### Dashboard shows API errors (500s)
**Symptom**: Dashboard actions fail with server errors.
**Fix**: Check Terminal 2 (MCP REST API) for Python tracebacks. Common causes:
database connection issues, missing project, scan in progress.

### Dashboard can't connect to API
**Symptom**: Network errors in browser console.
**Fix**: Ensure MCP REST API is running on port 8001:
`cd mcp-server && python -m mcp_server --serve`

## RAG Pipeline Issues

### Ollama not running
**Symptom**: RAG push fails with connection error (server-side after generation, or manual `insert_to_rag`).
**Fix**: Start Ollama and pull the embedding model:
```bash
ollama pull nomic-embed-text
```

### Embedding failures
**Symptom**: Documents push but search returns no results.
**Fix**: Verify Ollama is responding: `curl http://localhost:11434/api/tags`.
Check that `nomic-embed-text` is in the model list.

### Database connection refused
**Symptom**: Alembic migration or API startup fails.
**Fix**: Ensure PostgreSQL is running. Check connection string in
environment variables or config files.

## General Tips

- Always check service logs in the terminal where the service was started
- Use `get_scan_status(project_id)` to verify scan state before generating docs
- The dashboard's log panel shows real-time output during scan and generation
- If a scan hangs, check for very large files that may cause the parser to stall
