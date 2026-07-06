---
name: validate
description: Run scoped validation for changed files. Use before committing to run py_compile, pytest, and TypeScript checks only for touched RAG or knowledge_base sub-projects and report pass/fail table.
---

# Validate

Run validation checks for the active sub-project using real command output.

## Determine Scope

Run:

```bash
git diff --name-only HEAD
git status --short
```

Combine both lists. Identify touched sub-projects and skip irrelevant checks.

## Checks

Run applicable commands from the workspace root:

- RAG backend Python syntax: changed `RAG_pipeline/backend/**/*.py`.
- RAG backend pytest: changed `RAG_pipeline/backend/**`.
- RAG frontend TypeScript: changed `RAG_pipeline/frontend/**`.
- KB Python syntax: changed `knowledge_base/**/*.py`.
- KB mcp-server pytest: changed `knowledge_base/mcp-server/**`.
- KB dashboard TypeScript: changed `knowledge_base/dashboard/**`.

Use project-local commands and report exact failures. Do not claim success without current output.

## Output

```markdown
| Check | Result | Detail |
|-------|--------|--------|
| rag py_compile | PASS | |
| rag pytest | SKIP | no backend changes |
| rag tsc | SKIP | no frontend changes |
| kb py_compile | PASS | |
| kb pytest | SKIP | no mcp-server changes |
| kb tsc | SKIP | no dashboard changes |
```

Result values: `PASS`, `FAIL`, or `SKIP`.

Final line:

- All PASS or SKIP: `PASS - ready to commit.`
- Any FAIL: `FAIL - fix before committing.` Then include full failing output.

