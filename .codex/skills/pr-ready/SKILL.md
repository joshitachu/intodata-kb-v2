---
name: pr-ready
description: Run the pre-PR readiness gate. Use before creating a pull request to check clean worktree, PR scope, CI, local validation, documentation gaps, blockers, and produce a PR summary.
---

# PR Ready

Run before creating a pull request. Stop at the first blocking issue.

## Steps

1. Check uncommitted changes:

```bash
git status --short
```

If any files are modified or untracked, print `BLOCKED - uncommitted changes` and list them.

2. Show PR scope:

```bash
git log --oneline -8
git diff main...HEAD --stat
```

3. Check GitHub CI if available:

```bash
gh pr checks 2>/dev/null || echo "no PR open yet - skipping CI check"
```

If a PR exists and checks fail, block on CI.

4. Run `validate` logic for touched sub-projects.
5. Check documentation gaps for new files in routes, models, MCP server code, and dashboard components.
6. Read context files for active blockers.

## Success Output

```markdown
READY - PR checklist passed.

Commits in this PR:
  <git log --oneline output>

Suggested PR title: <derived title>

## Summary
- <logical change>

## Test plan
- [ ] validate passed
- [ ] Manual smoke test on affected area
- [ ] No regressions in affected sub-projects
```

If blocked, print exact output and the required fix.

