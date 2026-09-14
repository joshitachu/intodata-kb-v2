# ADR-016: RBAC Is Implemented Twice Against One Schema; Both Sides Must Change Together

## Status
Accepted (documented 2026-04-20); still current

## Context

The MCP server and the RAG backend are separate services that read the same PostgreSQL
tables (`users`, `user_roles`, `role_permissions`, `permissions`, `project_assignments`).
Each has its own access-control code path.

## Decision

This duplication is accepted, not eliminated. The invariant is that any change to the
permission model must be made in both services.

## Rationale

"The MCP server and the RAG backend each have their own RBAC code path against the same
PostgreSQL tables. RAG backend uses SQLAlchemy via `get_user_project_ids()` in
`RAG_pipeline/backend/src/rbac/permissions.py`; MCP server uses raw-SQL helpers in
`mcp-server/src/mcp_server/api/routes/projects.py` (`_get_user_is_superadmin`,
`_get_user_permission_codenames`, `_get_assigned_project_ids`) plus
`require_project_access()` in `mcp-server/src/mcp_server/api/permissions.py`. Any change to
the permission model (new codename, new role, change to assignment semantics) must be made
in BOTH services or the two surfaces will disagree."

"Routes that need RBAC on the MCP side are easy to miss. Router-level
`dependencies=[Depends(require_auth)]` only guarantees the user is authenticated, not that
they can see the `project_id`. Every new route under `/projects/{project_id}/...` must
either call `await require_project_access(request, project_id)` explicitly, or filter its
output by user assignment."

## Consequences

- Three routes were found in 2026-04 accepting any `project_id` from any authenticated
  user; router-level auth had made them look protected.
- Authority is keyed on permissions, not role names, so a renamed admin role still
  resolves.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Lessons Learned` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
