# SOP: Add a New Project

How to register and scan a new integration project for documentation generation.

## Steps

1. **Place source code** in a directory accessible to the scanner.
   Can be a local directory, git URL, or ZIP file.

2. **Register the project** via MCP:
   ```
   add_repo(url="<path-or-url>", project_id="<unique-id>", platform="talend")
   ```
   Or via REST API:
   ```bash
   curl -X POST http://localhost:8001/mcp/add-repo \
     -H "Content-Type: application/json" \
     -d '{"url": "<path>", "project_id": "<id>", "platform": "talend"}'
   ```
   Or via dashboard: click "Add Repo" and fill in the form.

3. **Trigger a scan**:
   ```
   trigger_scan(project_id)
   ```

4. **Verify the scan**:
   ```
   get_scan_status(project_id)   -- Check status and job count
   get_chunks(project_id)        -- Verify chunks were created
   ```

5. **Run documentation generation** following `sop/run-doc-generation.md`.

## Platform Support

Currently supported:
- **Talend** (`--platform talend`) -- Parses Talend XML/XMI job definitions

Planned (architecture supports extension):
- MuleSoft
- dbt
- Other integration platforms

Each platform has its own parser in `intodoc/src/intodoc/platforms/<name>/`.

## Source Types

| Type | URL parameter | Notes |
|------|---------------|-------|
| Local directory | `/path/to/source` | Must be accessible from the server |
| Git repository | `https://...` or `git@...` | Cloned automatically |
| ZIP upload | `zip` | Upload via `/mcp/upload-zip` REST endpoint first |
