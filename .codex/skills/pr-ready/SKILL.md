---
name: pr-ready
description: Pre-PR gate that stops at the first blocking issue - uncommitted changes, failing CI, failing local checks, then doc gaps and blockers as warnings. Use before opening any pull request.
---

# PR Ready

Stops at the first blocker and names the fix. Do not continue past a blocked step.

```bash
REPO=$(git rev-parse --show-toplevel)
BASE=$(git -C "$REPO" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|origin/||')
BASE=${BASE:-master}
```

Derive the base branch -- do not assume `main`. This repo's default is `master`, and
a hardcoded `main` produces an empty diff that looks like a clean PR.

## 1. Uncommitted changes

```bash
git -C "$REPO" status --short
```

Any output -> **BLOCKED: uncommitted changes.** List them. Commit or stash. Stop here.

## 2. PR scope

```bash
git -C "$REPO" log --oneline "$BASE"..HEAD
git -C "$REPO" diff "$BASE"...HEAD --stat
```

Print both. If the commit list is empty, **BLOCKED: nothing to PR** -- you are on the
base branch or have no commits ahead of it.

## 3. CI

```bash
gh pr checks 2>/dev/null || echo "no PR open yet -- skipping"
```

Any failing check -> **BLOCKED: CI failing.** Print the check names and statuses.

## 4. Local validation

Run the `validate` skill. Any FAIL or ERROR -> **BLOCKED: local checks failing.**
Print the full output. Do not continue.

## 5. Documentation gaps (warning)

```bash
git -C "$REPO" diff --name-only "$BASE"...HEAD
```

For each **new** file, check it is mentioned where it belongs:

| New file | Should appear in |
|---|---|
| `RAG_pipeline/backend/src/api/routes/*.py` | `RAG_pipeline/CLAUDE.md` routes |
| `RAG_pipeline/backend/src/db/models/*.py` | `RAG_pipeline/CLAUDE.md` models |
| `knowledge_base/mcp-server/src/**/*.py` | `knowledge_base/CLAUDE.md` |
| `knowledge_base/dashboard/src/components/*.tsx` | `knowledge_base/CLAUDE.md` |

Gaps -> **WARNING: undocumented files.** Not a block; record it.

## 6. Blockers (warning)

Read `context.md` for each touched sub-project. A non-empty blockers section ->
**WARNING: active blockers.** List them.

## 7. Output

On pass:

```
READY -- PR checklist passed.

Commits:
  <git log output>

Suggested title: <derived from the dominant change>

## Summary
- <one bullet per logical change>

## Test plan
- [ ] validate passed (paste the real table)
- [ ] Manual check on <the affected surface>
```

On failure: print each blocker with its exact output and the action that clears it.
Report warnings even on a pass -- a green gate that hid a warning is not useful.
