Run validation checks for the active sub-project. Uses real shell output -- report
actual pass/fail, not a narrative.

## Steps

### 1. Identify changed files

```bash
git diff --name-only HEAD
git status --short
```

Combine both lists (staged + unstaged). Determine which sub-projects are affected.

### 2. Run checks per sub-project

Run only the checks relevant to what changed. Skip checks for unchanged sub-projects.

#### knowledge_base Python (any `knowledge_base/**/*.py` changed)

```bash
# Syntax check each changed .py file
for f in $(git diff --name-only HEAD | grep "knowledge_base.*\.py$"); do
  python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"
done
```

```bash
# Unit tests (if mcp-server files changed)
cd knowledge_base/mcp-server && python3 -m pytest tests/ -x -q 2>&1 | tail -25
```

#### knowledge_base TypeScript (any `knowledge_base/dashboard/**` changed)

```bash
cd knowledge_base/dashboard && npx tsc --noEmit 2>&1 | head -40
```

#### RAG_pipeline Python (any `RAG_pipeline/backend/**` changed)

```bash
cd RAG_pipeline/backend && python3 -m pytest tests/ -x -q 2>&1 | tail -25
```

#### RAG_pipeline TypeScript (any `RAG_pipeline/frontend/**` changed)

```bash
cd RAG_pipeline/frontend && npx tsc --noEmit 2>&1 | head -40
```

### 3. Report

Output a table:

| Check | Result | Output |
|-------|--------|--------|
| kb py_compile | PASS / FAIL | (first error line if FAIL) |
| kb pytest | PASS / FAIL | (summary line) |
| kb tsc | PASS / FAIL | (first error if FAIL) |
| rag pytest | PASS / FAIL | (summary line) |
| rag tsc | PASS / FAIL | (first error if FAIL) |

If all pass: "PASS -- ready to commit."
If any fail: "FAIL -- fix before committing." List each failure with the relevant output.
