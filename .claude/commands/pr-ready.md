Pre-PR gate. Run before creating any pull request. Stops at the first blocking issue
and tells you exactly what to fix.

### Steps

#### 1. Check for uncommitted changes

```bash
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 status --short
```

If any modified or untracked files: **BLOCKED — uncommitted changes.** List them.
Commit or stash before continuing. Do not proceed past this step if blocked.

#### 2. Show PR scope

```bash
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 log --oneline -8
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 diff main...HEAD --stat
```

Print both outputs. This shows exactly what is going into the PR.

#### 3. Run CI checks (if on GitHub)

```bash
gh pr checks 2>/dev/null || echo "no PR open yet — skipping CI check"
```

If a PR is open and any check is failing: **BLOCKED — CI failing.** Print the check names
and their status. Do not continue until CI is green.

#### 4. Run local validation

Run the full `/validate` logic inline (same bash blocks as validate.md):
- py_compile on all changed Python files for each touched sub-project
- pytest for each touched sub-project
- tsc --noEmit for each touched frontend

If any check fails: **BLOCKED — local checks failing.** Print the full output.
Do not continue to step 5 until this passes.

#### 5. Check for documentation gaps

```bash
git -C /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2 diff --name-only main...HEAD
```

For each new file in the diff, check whether it appears in the relevant CLAUDE.md:
- New `RAG_pipeline/backend/src/api/routes/*.py` → check `RAG_pipeline/CLAUDE.md` routes section
- New `RAG_pipeline/backend/src/db/models/*.py` → check `RAG_pipeline/CLAUDE.md` models list
- New `knowledge_base/mcp-server/src/**/*.py` → check `knowledge_base/CLAUDE.md`
- New `knowledge_base/dashboard/src/components/*.tsx` → check `knowledge_base/CLAUDE.md`

If gaps found: **WARNING — undocumented files.** List them with the CLAUDE.md section they
belong in. This is a warning, not a block — but record it.

#### 6. Check for active blockers

Read `RAG_pipeline/.claude/context.md` and `knowledge_base/.claude/context.md`.
If either has a non-empty Blockers section: **WARNING — active blockers recorded.** List them.

#### 7. Output

If all checks pass (or only warnings):

```
READY — PR checklist passed.

Commits in this PR:
  <git log --oneline output>

Suggested PR title: <derive from the dominant change in git log>

## Summary
- <one bullet per logical change>

## Test plan
- [ ] /validate passed (pytest + tsc)
- [ ] Manual smoke test on affected area
- [ ] No regressions in [list affected sub-projects]
```

If anything failed: print each blocker with the exact output and the action needed to fix it.
