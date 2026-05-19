Log this session to PROGRESS_LOG.md and update context files. Uses real git output
to populate the entry -- do not rely on memory for file paths or change descriptions.

## Steps

### 1. Gather real data first (run all three)

```bash
git diff --stat HEAD          # exact list of changed files + line counts
git diff HEAD                 # full diff -- read to understand what changed and why
git log --oneline -3          # recent commit context
```

### 2. Determine sub-project(s)

From the `git diff --stat` output, identify which sub-project(s) were touched
(`knowledge_base/`, `RAG_pipeline/`, or both). Work on each separately below.

### 3. Append to `.claude/PROGRESS_LOG.md` for each touched sub-project

Use this format. Populate "Files changed" from `git diff --stat` output exactly --
do not paraphrase or omit files.

```
## Session: YYYY-MM-DD

### [Fix/Feature/Refactor]: <short description>

- **Files changed**: list each file with path (from git diff --stat)
- **Problem**: what was being solved or built
- **Root cause**: (bugs) why it existed; (features) what drove the need
- **Change**: exactly what was added/modified/removed
- **Why this approach**: the reasoning
- **Verification**: how it was confirmed working (tests run, manual check, etc.)
```

### 4. Update `.claude/context.md` for each touched sub-project

- Set "Last completed" to reflect what was just done (1-2 lines)
- Update "Immediate Next Steps" -- replace the top item if it was completed
- Clear any blockers that were resolved

### 5. Distillation (do not skip)

Scan `git diff HEAD` for:
- Non-obvious invariants a future agent would need to know
- Workarounds for specific bugs or external constraints
- Architecture decisions that weren't captured in PROJECT_MEMORY.md

If found, add a row to the relevant `.claude/PROJECT_MEMORY.md` decisions table or
append to the Lessons Learned section. If nothing non-obvious was found, skip.

### 6. Report back

Output: "Session logged. Updated: [list of files written]"

Do not skip any changed file. A file touched = a file logged.
