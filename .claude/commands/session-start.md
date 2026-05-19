Load session context for the active sub-project and produce a concise brief.

## Argument: $ARGUMENTS

Accepted values: `kb` | `rag` | empty (default: both)

## Steps

1. Run `git status --short` to see modified files. Use this to confirm which sub-project
   is active (regardless of $ARGUMENTS, flag if git status contradicts the argument).

2. Load context based on $ARGUMENTS:
   - **`kb`** (or git status shows only knowledge_base changes):
     Read `knowledge_base/.claude/context.md` and the last 3 entries of
     `knowledge_base/.claude/PROGRESS_LOG.md`. Also read `knowledge_base/.agent/README.md`
     to identify relevant SOPs for the current work.
   - **`rag`** (or git status shows only RAG_pipeline changes):
     Read `RAG_pipeline/.claude/context.md` and the last 3 entries of
     `RAG_pipeline/.claude/PROGRESS_LOG.md`.
   - **empty / both**:
     Read context.md and last 3 PROGRESS_LOG entries for both sub-projects.

3. Output a brief (max 20 lines per active sub-project) with:
   - **Status**: what was last completed and where things stand
   - **Last 3 changes**: one line each from PROGRESS_LOG
   - **Blockers**: anything flagged as blocked
   - **Next step**: the single immediate priority from context.md
   - **Modified files**: from git status, grouped by sub-project

If the user asks about the backlog, deferred tasks, or future work, also read the
relevant `knowledge_base/.claude/backlog.md` or `RAG_pipeline/.claude/backlog.md` --
these are NOT loaded by default to keep startup context lean.
