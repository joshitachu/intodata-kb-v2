## Linked Issue

Closes #

## Description

<!-- What changed and why -->

## Type of Change

- [ ] Feature (new functionality)
- [ ] Fix (bug fix)
- [ ] Docs (documentation only)
- [ ] Refactor (code change, no bug fix or new feature)
- [ ] Chore (maintenance, dependencies, tooling)

## Testing Performed

<!-- Which validation commands did you run? See CONTRIBUTING.md for the list. -->

- [ ] `python3 -m py_compile <changed files>` (if Python files changed)
- [ ] `cd RAG_pipeline/backend && pytest` (if RAG backend changed)
- [ ] `cd RAG_pipeline/frontend && npx tsc --noEmit && npm test` (if RAG frontend changed)
- [ ] `cd knowledge_base/dashboard && npx tsc --noEmit && npm test` (if dashboard changed)
- [ ] `cd knowledge_base/mcp-server && pytest` (if MCP server changed)
- [ ] N/A (no code changes)

## Checklist

- [ ] Branch follows naming convention (`feature/*`, `fix/*`, `chore/*`)
- [ ] Commit messages follow conventional format (`Type: description`)
- [ ] Documentation updated if needed
- [ ] No hardcoded credentials or secrets
