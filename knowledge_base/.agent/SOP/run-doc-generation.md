# SOP: Run Documentation Generation

End-to-end guide for generating documentation for a project using the MCP agent workflow.

## Prerequisites

- MCP server configured in `.mcp.json` and accessible to Claude Code
- Project registered and scanned (see `sop/add-new-project.md` if not)
- Optionally: RAG pipeline running if you want to push docs afterward

## Steps

1. **Verify scan cache is fresh**
   ```
   get_scan_status(project_id)
   ```
   If stale or missing, run `trigger_scan(project_id)`.

2. **Load chunk definitions**
   ```
   get_chunks(project_id)
   ```
   Note the groups and chunk IDs returned.

3. **Follow the 4-phase workflow**
   Read `.agent/system/mcp-workflow.md` for the complete workflow:
   - Phase 1: Setup (manifest, connections)
   - Phase 2: Group Analysis (parallel sub-agents per group)
   - Phase 3: Write Docs (parallel sub-agents by theme)
   - Phase 4: Publish (index.json, run log, insert_to_rag)

4. **Verify output**
   Check `output/{project_id}/docs/` for generated markdown files.
   Verify `index.json` lists all generated documents.

5. **Push to RAG** (optional)
   ```
   insert_to_rag(project_id)
   ```

6. **Log the run**
   Update `.claude/PROGRESS_LOG.md` with:
   - Date and project ID
   - Documents generated
   - Any warnings or errors
   - Lessons learned

## Output

```
output/{project_id}/
  docs/
    00_PROJECT_OVERVIEW.md
    01_COMPONENT_INVENTORY.md
    ... (up to 25 documents, only those relevant to the project)
    index.json
  logs/
    run_{project_id}_{timestamp}.log
```
