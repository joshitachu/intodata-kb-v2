# Project Memory

Architecture decisions, lessons learned, and persistent context for the intodoc platform.

## Key Decisions

### MCP Agent > CLI Analyzer (Feb 2026)
Claude Code + MCP replaced the CLI-based LLM analyzer as the primary documentation
generation method. No API keys needed, better quality, parallel sub-agents.
See `.agent/decisions/001-mcp-over-cli.md` for full rationale.

### Documentation Structure (Feb 2026)
Adopted AI Jason's .agent folder pattern:
- `CLAUDE.md` = concise rules + pointers (~100 lines)
- `.agent/README.md` = smart index with "when to read what" table
- `.agent/system/` = architecture, workflow, tech stack
- `.agent/sop/` = numbered step-by-step procedures
- `.agent/decisions/` = Architecture Decision Records
- `.agent/tasks/` = Feature Requirement Documents (phase FRDs)

## Gotchas

- Windows cp1252 encoding breaks Unicode -- always use ASCII in output
- tMap distiller must strip noise BEFORE chunking to avoid token bloat
- ENDPOINT/URL parameters are semantic -- never strip them
- Connection matching uses REPOSITORY_PROPERTY_TYPE, not name heuristics
- intodoc uses hatchling build system, NOT setuptools
- Legacy CLI budget default is $5; can be exceeded with large projects

## Platform Identity

intodoc is a **multi-project, multi-platform** code intelligence tool.
Documentation should never be specific to one project (e.g., vaxxinova) or
one platform (e.g., Talend) unless explicitly scoped to that context.

## Phase Status

- Phase 0: COMPLETE (cleanup + knowledge base)
- Phase 1: COMPLETE (21 analysis passes, 32 templates, P19-P21)
- Phase 2: COMPLETE (Universal IR, 99.4% expression parse rate)
- Phase 3: NOT STARTED (migration code generation)
