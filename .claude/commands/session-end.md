Write a PROGRESS_LOG entry for this session and update context files. This enforces the mandatory logging rules without manual effort.

Steps:
1. Run `git diff --name-only HEAD` and `git status --short` to get the list of changed files this session.
2. Determine which sub-project(s) were touched (RAG_pipeline or knowledge_base).
3. For each sub-project with changes, append a new entry to its `.claude/PROGRESS_LOG.md` using this format:

```
## Session: YYYY-MM-DD

### [Fix/Feature/Refactor]: <Short description of what was done>

- **Files changed**: list each file with path
- **Problem**: what was being solved or built
- **Root cause**: (for bugs) why the problem existed; (for features) what drove the need
- **Change**: exactly what was added/modified/removed
- **Why this approach**: the reasoning
- **Verification**: how it was confirmed working (tests run, manual check, etc.)
```

4. Update `.claude/context.md` for each touched sub-project:
   - Set "Current state" to reflect what was just completed
   - Update "Next steps" based on what remains
   - Clear any blockers that were resolved

5. If an architecture decision was made (new pattern, new module, new integration), add a row to the decisions table in `.claude/PROJECT_MEMORY.md`.

6. Report back: "Session logged. Files updated: [list]"

Do not skip any changed files. A file touched = a file logged.
