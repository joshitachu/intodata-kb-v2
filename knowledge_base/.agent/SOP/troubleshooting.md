# Troubleshooting Guide

Common issues and fixes for the intodoc platform.

## Scanner Issues

The scanner's known pitfalls (cp1252 encoding, token bloat, ENDPOINT preservation,
connection matching, hatchling) are listed once in `knowledge_base/CLAUDE.md`
"Known Pitfalls".

## MCP Server Issues

### MCP server not connecting
**Symptom**: An MCP client can't find intodoc MCP tools.
**Fix**: Verify the client's (local, untracked) MCP config runs
`python -m mcp_server --mcp` with `cwd` set to `knowledge_base/mcp-server/`. Restart the
MCP client to reload its config.

### "Project not found" errors
**Symptom**: MCP tools return `{"error": "Project not found: ..."}`.
**Fix**: Register the project first with `add_repo()`. Check `list_projects()`
to see what's registered.

### Scan fails with "intodoc command not found"
**Symptom**: `trigger_scan()` fails because intodoc CLI is not installed.
**Fix**: Install the scanner: `cd intodoc && pip install -e .`

### Scan succeeds but project status becomes error
**Symptom**: Scan subprocess exits 0, but MCP marks scan failed with
"did not produce scan_result.json" or "invalid scan_result.json".
**Fix**: This is intentional fail-closed behavior. `ScannerService` deletes stale
scan artifacts before each scan and requires a fresh valid `scan_result.json`.
Check the intodoc stdout/stderr log for parser failures, output path mismatch, or
permission issues. Do not copy an old scan file into place to bypass the gate.

### Enterprise generation fails and leaves docs_staging
**Symptom**: Generation ends in error, but `output/{project_id}/docs_staging/`
still exists with docs and `_assessment/enterprise_gates.json`.
**Fix**: This is expected. Enterprise mode preserves staging for repair/resume and
does not publish to `docs/` until all gates pass. Inspect:
```bash
jq '.gates' output/{project_id}/docs_staging/_assessment/enterprise_gates.json
```
Then repair the listed gate failures and generate again with resume enabled.

### Docs exist but are not client-ready
**Symptom**: Run status is completed or docs are visible, but dashboard verdict is
`draft`, `published`, or `published_with_rag_error`.
**Fix**: Only `client_ready` means delivery-ready. For `published_with_rag_error`,
repair RAG and re-sync. For `draft`, regenerate in enterprise mode. For
`published`, inspect `gate_results` to identify which readiness condition was not
satisfied.

## Dashboard Issues

### Dashboard shows API errors (500s)
**Symptom**: Dashboard actions fail with server errors.
**Fix**: Check the MCP REST API terminal for Python tracebacks. Common causes:
database connection issues, missing project, scan in progress.

### Dashboard can't connect to API
**Symptom**: Network errors in browser console.
**Fix**: Ensure MCP REST API is running on port 8002 (the `--serve` default):
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
