---
name: doc-gap
description: Audit changed files for documentation gaps. Use after a feature to compare git changes against CLAUDE.md documentation coverage and recommend where missing entries belong.
---

# Documentation Gap Audit

Audit changed files that are absent from project documentation.

## Steps

1. Run `git status --short`.
2. Run `git diff --name-only HEAD`.
3. Combine modified and untracked files.
4. For each file, check whether its path, module name, or component name appears in relevant `CLAUDE.md` files:
   - `CLAUDE.md`
   - `RAG_pipeline/CLAUDE.md`
   - `knowledge_base/CLAUDE.md`
   - `knowledge_base/intodoc/CLAUDE.md` if present
5. Specifically inspect common gaps:
   - New `RAG_pipeline/backend/src/api/routes/` files.
   - New `RAG_pipeline/backend/src/db/models/` files.
   - New `RAG_pipeline/backend/src/services/` files.
   - New `RAG_pipeline/frontend/components/` components.

## Output

Return a table:

```markdown
| File | Status | Gap Type |
|------|--------|----------|
| path/to/file | UNDOCUMENTED | New route |
| path/to/file | DOCUMENTED | - |
```

Then suggest the exact documentation file and section for each undocumented file.

