Audit the current codebase for documentation gaps — files that exist in code but are not mentioned in any CLAUDE.md file.

Steps:
1. Run `git status --short` to get all modified and untracked files.
2. Run `git diff --name-only HEAD` to get files changed since last commit.
3. For each file in the combined list:
   a. Check if the file path, module name, or component name appears in any of these CLAUDE.md files:
      - `CLAUDE.md` (root)
      - `RAG_pipeline/CLAUDE.md`
      - `knowledge_base/CLAUDE.md`
      - `knowledge_base/intodoc/CLAUDE.md`
   b. Flag it as UNDOCUMENTED if there is no mention.
4. Also check for these specific known gaps:
   - New files in `RAG_pipeline/backend/src/api/routes/` not listed in the routes section
   - New files in `RAG_pipeline/backend/src/db/models/` not in the models list
   - New files in `RAG_pipeline/backend/src/services/` not in the services list
   - New frontend components in `RAG_pipeline/frontend/components/` not referenced

Output a table:
| File | Status | Gap Type |
|------|--------|----------|
| path/to/file | UNDOCUMENTED | New route / New model / New component / etc. |
| path/to/file | DOCUMENTED | - |

Then suggest which CLAUDE.md section each undocumented file belongs in.

This command is most useful after completing a feature — run it before committing to catch doc gaps early.
