Run validation checks for the active sub-project. Uses real shell output — report
actual pass/fail, not a narrative.

### Steps

#### 1. Determine scope

```bash
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 diff --name-only HEAD
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 status --short
```

Combine both lists. Identify which sub-projects are touched. Only run checks relevant
to what changed — skip everything else (mark as SKIP in the table).

#### 2. Run each applicable check and capture output

##### RAG backend — Python syntax (any `RAG_pipeline/backend/**/*.py` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 && for f in $(git diff --name-only HEAD | grep "^RAG_pipeline/backend/.*\.py$" | head -15); do python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"; done
```

##### RAG backend — pytest (any `RAG_pipeline/backend/**` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/RAG_pipeline/backend && python3 -m pytest tests/ -x -q 2>&1 | tail -20
```

##### RAG frontend — tsc (any `RAG_pipeline/frontend/**` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/RAG_pipeline/frontend && npx tsc --noEmit 2>&1 | head -30
```

##### KB Python — syntax (any `knowledge_base/**/*.py` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 && for f in $(git diff --name-only HEAD | grep "^knowledge_base/.*\.py$" | head -15); do python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"; done
```

##### KB mcp-server — pytest (any `knowledge_base/mcp-server/**` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/knowledge_base/mcp-server && python3 -m pytest tests/ -x -q 2>&1 | tail -20
```

##### KB dashboard — tsc (any `knowledge_base/dashboard/**` changed)

```bash
cd /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/knowledge_base/dashboard && npx tsc --noEmit 2>&1 | head -30
```

#### 3. Emit the result table

After all checks complete, output exactly this format:

```
| Check               | Result | Detail                           |
|---------------------|--------|----------------------------------|
| rag py_compile      | PASS   |                                  |
| rag pytest          | PASS   | 42 passed in 3.2s                |
| rag tsc             | SKIP   | no frontend changes              |
| kb py_compile       | FAIL   | SyntaxError line 14: invalid ... |
| kb pytest           | PASS   | 18 passed in 1.1s                |
| kb tsc              | SKIP   | no dashboard changes             |
```

Result values:
- **PASS** — exit code 0, no errors in output
- **FAIL** — exit code non-zero or error text — put the first error line in Detail
- **SKIP** — sub-project not touched by this change

Final line (required):
- All PASS or SKIP → `PASS — ready to commit.`
- Any FAIL → `FAIL — fix before committing.` then print the full output for each failing check.
