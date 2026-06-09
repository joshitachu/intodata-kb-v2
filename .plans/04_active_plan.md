---
feature: EP-008 Repo and Team Readiness
slice: All slices (1-4)
sub_project: repository governance
critical_area: no
verdict: PASS
approved_at: 2026-06-09
---

## Problem Statement
No documented workflow exists for branching, development, or PR process. No PR template, no CODEOWNERS, no CI validation, and no branch protection. Developers push directly to master with no conventions.

## Proposed Solution
Four deliverables in one session:
1. `CONTRIBUTING.md` — branching strategy, workflow, commit conventions
2. `.github/PULL_REQUEST_TEMPLATE.md` + `.github/CODEOWNERS` — PR structure and ownership
3. `.github/workflows/pr-validation.yml` — automated checks on every PR
4. Branch protection rules on `master` via `gh` CLI

## Affected Files
- `CONTRIBUTING.md` (new)
- `.github/PULL_REQUEST_TEMPLATE.md` (new)
- `.github/CODEOWNERS` (new)
- `.github/workflows/pr-validation.yml` (new)

## Architecture Contracts
- CONTRIBUTING.md references AGENTS.md for validation commands (no duplication)
- CONTRIBUTING.md references `knowledge_base/.agent/SOP/setup-dev-environment.md` for dev setup (no duplication)
- ASCII-only Markdown throughout
- CI workflow must not require Docker or external services — both Python test suites use `sqlite+aiosqlite:///:memory:` (confirmed from conftest.py files)

## Implementation Steps

### Task 1: CONTRIBUTING.md

Create `CONTRIBUTING.md` at repo root with:

**Branching strategy table:**
| Type | Pattern | When |
|------|---------|------|
| Feature | `feature/<short-description>` | New functionality |
| Fix | `fix/<short-description>` | Bug fix |
| Chore | `chore/<short-description>` | Maintenance, docs, refactoring |

- Branch from `master`, lowercase with hyphens, delete after merge

**Development workflow** (numbered):
1. `git checkout master && git pull`
2. `git checkout -b feature/my-change`
3. Make changes
4. Run validation (link to AGENTS.md commands section)
5. Commit with conventional message
6. `git push origin feature/my-change`
7. Open PR to `master`
8. Address review, merge after checks pass

**Commit format**: `<Type>: <short description>`
Types: `Feat`, `Fix`, `Docs`, `Refactor`, `Test`, `Chore`

**PR expectations**: linked issue, description, testing performed

**References**: AGENTS.md (commands + conventions), setup-dev-environment.md (setup), readme.md (quick start)

### Task 2: PR template + CODEOWNERS

**`.github/PULL_REQUEST_TEMPLATE.md`** with sections:
- Linked issue/story
- Description of change
- Type of change (checkboxes: feature, fix, docs, refactor, chore)
- Testing performed
- Checklist (validation run, docs updated if needed, branch naming followed)

**`.github/CODEOWNERS`** mapping:
```
*                           @joshitachu
RAG_pipeline/               @joshitachu
knowledge_base/             @joshitachu
mission_control/            @joshitachu
```
Single owner for now — expand when team grows.

### Task 3: PR validation CI workflow

**`.github/workflows/pr-validation.yml`** — triggers on PR to `master`.

Five parallel jobs:

1. **python-syntax**: `find . -name "*.py" -newer ... | xargs python3 -m py_compile` (or compile all .py files)
2. **rag-backend-tests**: `cd RAG_pipeline/backend && pip install -e ".[dev]" && pytest`
3. **rag-frontend-check**: `cd RAG_pipeline/frontend && npm ci && npx tsc --noEmit && npm test`
4. **dashboard-check**: `cd knowledge_base/dashboard && npm ci && npx tsc --noEmit && npm test`
5. **mcp-server-tests**: `cd knowledge_base/mcp-server && pip install -e ".[dev]" && pytest`

Runner: `ubuntu-latest`, Python 3.11, Node 20.

Key findings from codebase research:
- Both Python test suites use `sqlite+aiosqlite:///:memory:` — no Postgres/Qdrant needed
- RAG backend has `[dev]` extras with pytest, pytest-asyncio, aiosqlite, ruff
- MCP server has `[dev]` extras with pytest, pytest-asyncio, aiosqlite, ruff
- Frontend uses `vitest run` for tests, `tsc --noEmit` for typecheck
- Dashboard uses `vitest run` for tests, `tsc --noEmit` for typecheck
- MCP server uses hatchling build system (not setuptools)

### Task 4: Branch protection rules

Use `gh` CLI to configure:
```bash
gh api repos/joshitachu/intodata-kb-v2/branches/master/protection \
  --method PUT \
  --field required_status_checks='{"strict":true,"contexts":["python-syntax","rag-backend-tests","rag-frontend-check","dashboard-check","mcp-server-tests"]}' \
  --field enforce_admins=true \
  --field required_pull_request_reviews='{"required_approving_review_count":0}' \
  --field restrictions=null \
  --field allow_force_pushes=false \
  --field allow_deletions=false
```

`required_approving_review_count: 0` allows solo developer self-merge after checks pass.

### Task 5: End-to-end validation

1. Create a test branch: `git checkout -b chore/test-governance`
2. Make a trivial change (e.g., add a line to readme.md)
3. Commit with conventional message: `Chore: test governance setup`
4. Push and open a PR to `master`
5. Verify:
   - PR template renders
   - CODEOWNERS assigns reviewer
   - CI workflow runs and all 5 jobs pass
   - Branch protection blocks merge until checks pass
6. Merge the test PR
7. Clean up test branch

## Verification Strategy
1. All 5 CI jobs pass on the test PR
2. Branch protection prevents direct push to master (test by attempting `git push origin master`)
3. PR template sections are visible when opening a PR
4. CODEOWNERS auto-assigns reviewer
5. CONTRIBUTING.md links resolve correctly

## Out of Scope
- Secret scanning
- Deployment pipeline
- Path-based CI filtering
- Modifying AGENTS.md or setup-dev-environment.md
- Changes to `dev`/`production` branches
