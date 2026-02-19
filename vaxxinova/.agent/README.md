# intodoc Platform -- Agent Documentation Index

## When to Read What

| You are...                        | Read these files                                    |
|-----------------------------------|-----------------------------------------------------|
| Starting any session              | This file + `.claude/PROGRESS_LOG.md`               |
| Generating documentation          | `system/mcp-workflow.md`, `sop/run-doc-generation.md` |
| Understanding the architecture    | `system/architecture.md`, `system/tech-stack.md`    |
| Adding a new project to scan      | `sop/add-new-project.md`                            |
| Working on the scanner (intodoc)  | `intodoc/.agent/README.md`, `intodoc/CLAUDE.md`     |
| Working on the MCP server         | `system/mcp-server.md`                              |
| Working on the dashboard          | `system/dashboard.md`                               |
| Setting up the dev environment    | `sop/setup-dev-environment.md`                      |
| Debugging an issue                | `sop/troubleshooting.md`                            |
| Planning a new phase/feature      | Check `tasks/` for existing FRDs first              |
| After completing a feature        | Run `/update-doc` to update documentation           |

## Directory Contents

### system/ -- How things work
- `architecture.md` -- Platform architecture, components, data pipeline
- `mcp-workflow.md` -- 4-phase MCP documentation generation workflow (moved from CLAUDE.md)
- `tech-stack.md` -- Technology stack reference (Python, React, databases)
- `mcp-server.md` -- MCP tools, REST API endpoints, configuration
- `dashboard.md` -- Dashboard components, features, API integration

### sop/ -- How to do things
- `run-doc-generation.md` -- End-to-end guide: scan -> analyze -> write docs -> RAG
- `add-new-project.md` -- Register and scan a new project
- `setup-dev-environment.md` -- Get the platform running locally
- `troubleshooting.md` -- Common issues and fixes

### tasks/ -- Feature Requirement Documents
- `phase0-cleanup.md` -- Project cleanup & knowledge base setup (COMPLETE)
- `phase1-docs-improvement.md` -- Documentation quality & migration readiness (COMPLETE)
- `phase2-universal-ir.md` -- Universal intermediate representation (COMPLETE)
- `phase3-migration-generator.md` -- Migration code generation (NOT STARTED)

### decisions/ -- Architecture Decision Records
- `001-mcp-over-cli.md` -- Why MCP agent replaced CLI analyzer for doc generation

## Related Documentation
- `CLAUDE.md` (project root) -- Platform identity, rules, quick commands
- `intodoc/CLAUDE.md` -- Scanner CLI architecture and known pitfalls
- `intodoc/.agent/` -- Scanner-specific SOPs, ADRs, milestones
