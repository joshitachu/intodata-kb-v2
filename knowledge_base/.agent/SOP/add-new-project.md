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
   curl -X POST http://localhost:8002/mcp/add-repo \
     -H "Content-Type: application/json" \
     -d '{"url": "<path>", "project_id": "<id>", "platform": "talend"}'
   ```
   Or via dashboard: click "Add Repo" and fill in the form.

   For a private repository, pass the credential reference instead of a raw token
   where one exists -- `installation_id` for a GitHub App installation, or
   `azure_devops_connection_id` for an Azure DevOps organization connection (create
   one first via `POST /mcp/azure-devops/connections`). `github_token` remains the
   fallback. Azure DevOps browser URLs may be pasted as-is; `add-repo` reduces them
   to a bare clone URL. See `.agent/system/mcp-server.md` "Git connection kinds".

3. **Trigger a scan**:
   ```
   trigger_scan(project_id)
   ```

4. **Verify the scan**:
   ```
   get_scan_status(project_id)   -- Check status and job count
   get_chunks(project_id)        -- Verify chunks were created
   ```

5. **Run documentation generation** -- trigger via dashboard "Generate" button or `POST /mcp/generate`.

## Platform Support

| Platform | Value | Notes |
|----------|-------|-------|
| Talend | `talend` | XML/XMI job definitions |
| Workato | `workato` | Recipe JSON exports |
| Generic | `generic` | Flat file codebases |
| Lobster | `lobster` | Lobster Data integration |

Each platform has its own parser in `intodoc/src/intodoc/platforms/<name>/`.

## Source Types

| Type | URL parameter | Notes |
|------|---------------|-------|
| Local directory | `/path/to/source` | Must be accessible from the server |
| Git repository | `https://...` or `git@...` | Cloned automatically |
| ZIP upload | `zip` | Upload via `/mcp/upload-zip` REST endpoint first |
