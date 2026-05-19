Pre-PR gate. Run before creating any pull request. Stops at the first blocking issue
and tells you exactly what to fix.

## Steps

### 1. Check for uncommitted changes

```bash
git status --short
```

If any modified or untracked files exist: **BLOCKED -- uncommitted changes.** List them.
Tell the user to commit or stash before continuing.

### 2. Show what's going into the PR

```bash
git log --oneline -5
git diff main...HEAD --stat
```

Output the commit list and file summary so the scope is visible.

### 3. Run /validate inline

Run all relevant validation checks (same logic as `/validate`):
- py_compile on changed Python files
- pytest for each touched sub-project
- tsc --noEmit for each touched frontend

If any check fails: **BLOCKED -- tests/type checks failing.** Show the failure output.
Do not continue to step 4 until this passes.

### 4. Check for documentation gaps

Run the /doc-gap logic:
- `git diff --name-only main...HEAD` to get all changed files in this branch
- Check each changed file against the relevant CLAUDE.md files for coverage
- Flag any `UNDOCUMENTED` files (new routes, models, components not mentioned)

If gaps found: **WARNING -- undocumented files.** List them with suggested CLAUDE.md section.
This is a warning, not a block -- but log it.

### 5. Check for active blockers

Read `knowledge_base/.claude/context.md` and `RAG_pipeline/.claude/context.md`.
If either has a non-empty Blockers section: **WARNING -- active blockers recorded.**

### 6. Output

If all checks pass:

```
READY -- PR checklist passed.

Suggested PR title: <derive from git log>

Suggested description:
## Summary
- <bullet per commit>

## Test plan
- [ ] /validate passed (pytest + tsc)
- [ ] Manual smoke test on affected area
- [ ] No regressions in [list affected sub-projects]
```

If anything failed: list each blocker with the specific action needed to fix it.
