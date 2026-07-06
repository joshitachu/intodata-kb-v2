---
name: session-end
description: Log a session to PROGRESS_LOG.md and update context files. Use real git diff output to document files changed, problem, root cause, change, reasoning, verification, and durable lessons.
---

# Session End

Log the completed session using real git output, not memory.

## Gather Data

Run:

```bash
git diff --stat HEAD
git diff HEAD
git log --oneline -3
```

## Workflow

1. Determine touched sub-projects from `git diff --stat HEAD`.
2. For each touched sub-project, append to its `.claude/PROGRESS_LOG.md`:

```markdown
## Session: YYYY-MM-DD

### [Fix/Feature/Refactor]: <short description>

- **Files changed**: <exact paths from git diff --stat>
- **Problem**: <what was solved>
- **Root cause**: <why it existed or what drove the need>
- **Change**: <what changed>
- **Why this approach**: <reasoning>
- **Verification**: <tests or manual checks>
```

3. Update each touched `.claude/context.md`:
   - Last completed.
   - Immediate next steps.
   - Resolved blockers.
4. Scan `git diff HEAD` for durable invariants, workarounds, or architecture decisions. If found, update the relevant `.claude/PROJECT_MEMORY.md`.
5. Report `Session logged. Updated: [...]`.

Do not skip changed files.

