Review recent changes and update the .agent/ documentation system.

## Instructions

1. Read `.agent/README.md` to understand current doc structure
2. Check recent git changes: `git log --oneline -10` and `git diff --stat`
3. For each changed area, update the relevant documentation:
   - Architecture change -> update `.agent/system/architecture.md`
   - New workflow/process -> create `.agent/sop/{name}.md`
   - New decision -> create `.agent/decisions/{NNN}-{name}.md`
   - Task completed -> update status in `.agent/tasks/`
   - New tech/dependency -> update `.agent/system/tech-stack.md`
   - MCP server change -> update `.agent/system/mcp-server.md`
   - Dashboard change -> update `.agent/system/dashboard.md`
4. Update `.claude/PROGRESS_LOG.md` with a session entry:
   - Date, what was done, files changed, lessons learned
5. Update `.claude/PROJECT_MEMORY.md` if new decisions or gotchas discovered
6. Verify `.agent/README.md` index is complete and up to date
7. Report what was updated

$ARGUMENTS
