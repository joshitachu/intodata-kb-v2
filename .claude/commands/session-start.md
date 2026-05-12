Determine which sub-project the current work is focused on (RAG_pipeline or knowledge_base, or both), then read and summarize the session context for that area.

Steps:
1. Check `git status` to see which files are currently modified — this tells you which sub-project is active.
2. For each active sub-project, read:
   - `.claude/context.md` — current state and blockers
   - The last 5 entries in `.claude/PROGRESS_LOG.md`
   - The "Blockers" section of `.claude/PROJECT_MEMORY.md`
3. If knowledge_base work is active, also read `knowledge_base/.agent/README.md` to identify relevant SOPs.

Output a concise brief (no more than 30 lines) with:
- **Current state**: what was last worked on and where things stand
- **Last 3 changes**: from PROGRESS_LOG, summarized in one line each
- **Open blockers**: anything explicitly flagged as blocked
- **Suggested next step**: based on context.md "next steps" section
- **Modified files** (from git status): grouped by sub-project

This replaces the need to manually read all the context files at session start.
