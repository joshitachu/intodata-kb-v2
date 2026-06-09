# Contributing to IntoData KB

Multi-project platform: code intelligence scanner + RAG knowledge base with local LLMs.
This guide covers how to contribute code, from branching to merging.

## Prerequisites

See [Development Setup](knowledge_base/.agent/SOP/setup-dev-environment.md) for full environment configuration.

Quick summary:
- Python 3.11+
- Node.js 18+ and npm
- PostgreSQL (local or Docker)
- Ollama with `nomic-embed-text` model

## Branching Strategy

| Type | Pattern | When to use |
|------|---------|-------------|
| Feature | `feature/<short-description>` | New functionality |
| Fix | `fix/<short-description>` | Bug fix |
| Chore | `chore/<short-description>` | Maintenance, docs, refactoring |

Rules:
- Always branch from `master`
- Use lowercase with hyphens: `feature/add-pr-template`, not `feature/Add_PR_Template`
- Delete your branch after merge

## Development Workflow

1. Sync master:
   ```bash
   git checkout master
   git pull origin master
   ```

2. Create a branch:
   ```bash
   git checkout -b feature/my-change
   ```

3. Make your changes

4. Run validation for the sub-projects you touched (see [Validation Commands](#validation-commands))

5. Commit with a conventional message:
   ```bash
   git add -A
   git commit -m "Feat: add PR validation workflow"
   ```

6. Push and open a PR:
   ```bash
   git push origin feature/my-change
   ```
   Then open a pull request targeting `master`.

7. Address review feedback

8. Merge after checks pass and reviewer approves

## Commit Messages

Format: `<Type>: <short description>`

| Type | Use for |
|------|---------|
| `Feat` | New feature |
| `Fix` | Bug fix |
| `Docs` | Documentation only |
| `Refactor` | Code change, no bug fix or new feature |
| `Test` | Adding or updating tests |
| `Chore` | Maintenance tasks |

Examples:
```
Feat: add PR validation workflow
Fix: correct database connection timeout
Docs: update contributing guide
Refactor: extract query builder into separate module
Test: add unit tests for auth service
Chore: bump dependency versions
```

## Validation Commands

Run the commands relevant to your changes before committing. See [AGENTS.md](AGENTS.md) for the full reference.

| Sub-project | Command |
|-------------|---------|
| Any Python file | `python3 -m py_compile <file>` |
| RAG backend | `cd RAG_pipeline/backend && pytest` |
| RAG frontend | `cd RAG_pipeline/frontend && npx tsc --noEmit && npm test` |
| Dashboard | `cd knowledge_base/dashboard && npx tsc --noEmit && npm test` |
| MCP server | `cd knowledge_base/mcp-server && pytest` |

## Pull Requests

Every PR must include:
- **Linked issue or story** (if applicable)
- **Description** of what changed and why
- **Testing performed** — which validation commands you ran and their results
- **Type of change** — use the checkboxes in the PR template

Review expectations:
- Author addresses all reviewer feedback
- Reviewer approves or requests changes
- Solo developers may self-merge after all checks pass
- Delete your branch after merge

## Code Conventions

See [AGENTS.md](AGENTS.md) for full code conventions. Key rules:

### Python
- Type hints on every function signature
- Pydantic v2 for data models
- `async`/`await` for all I/O
- Never bare `except` — catch specific exceptions

### TypeScript
- Never `any` — use `unknown` and narrow
- Strict mode always
- Handle all three states: loading, error, data

### Security
- Never hardcode credentials — env vars only
- RBAC filter on every database/vector query
