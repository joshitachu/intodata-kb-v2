---
feature: EP-008 Repo and Team Readiness
pipeline_step: 2_prd
---

## Executive Summary
Establish repository governance for intodata-kb-v2 on GitHub. Add branch protection on `master`, a branching strategy, CODEOWNERS, PR template, contributing guide, and a PR validation CI pipeline via GitHub Actions. Done = a developer opens a PR that passes automated checks and gets reviewed before merge.

## Problem
All work goes directly to `master` with no branches, no pull requests, no CI, and no review process. There is no defined way of working for a new developer joining the project. Even as a solo developer, pushes are unstructured.

## Goals
1. `master` branch is protected: no direct pushes, no force-pushes, requires PR with status checks
2. Branch naming convention is defined and documented (e.g., `feature/*`, `fix/*`, `chore/*`)
3. CODEOWNERS file assigns review ownership for core subsystems
4. PR template enforces structure: story link, description, testing notes
5. Contributing guide documents the full workflow: branching, development setup, PR expectations, commit conventions
6. PR validation pipeline runs on every PR: syntax checks, tests, and typechecks for all sub-projects

## Non-Goals
- Secret scanning (GitGuardian or similar)
- Deployment pipeline (push to production/staging on merge)
- Path-based filtering (run all checks on every PR, not just changed sub-projects)
- Git-flow or release branch management beyond the basic branching strategy

## User Stories
1. As a developer, I want to create a feature branch with a clear naming convention, so my work is isolated and traceable.
2. As a developer, I want a PR template that tells me what to include, so reviews are consistent and complete.
3. As a maintainer, I want branch protection on master, so nothing merges without passing checks and review.
4. As a new team member, I want a contributing guide, so I can onboard without asking the maintainer to explain everything.
5. As a maintainer, I want CODEOWNERS to auto-assign reviewers, so the right person reviews changes in their area.
6. As a developer, I want automated checks to run on my PR, so I catch test failures and type errors before review.

## Functional Requirements
1. Branch protection rules on `master`: require PR, require status checks to pass, disallow force-push, disallow direct push
2. `.github/CODEOWNERS` file mapping subsystem directories to owner(s)
3. `.github/PULL_REQUEST_TEMPLATE.md` with sections: linked issue/story, description of change, type of change, testing performed, checklist
4. `CONTRIBUTING.md` at repo root covering: branch naming, development setup, PR process, commit message conventions, code review expectations
5. Branch naming convention documented in CONTRIBUTING.md: `feature/<short-description>`, `fix/<short-description>`, `chore/<short-description>`
6. GitHub Actions workflow (`.github/workflows/pr-validation.yml`) that runs on PR to `master`:
   - Python syntax check (`py_compile`) on all changed `.py` files
   - RAG backend tests (`pytest` in `RAG_pipeline/backend/`)
   - RAG frontend typecheck + test (`tsc --noEmit && npm test` in `RAG_pipeline/frontend/`)
   - Dashboard typecheck + test (`tsc --noEmit && npm test` in `knowledge_base/dashboard/`)
   - MCP server tests (`pytest` in `knowledge_base/mcp-server/`)
7. PR validation workflow is registered as a required status check in branch protection

## Non-Functional Requirements
- All governance files must live in the repository (not external docs)
- Branch protection must not block solo development (allow self-merge after checks pass)
- PR validation workflow must complete in under 5 minutes
- Workflow must not require Docker or external services (Qdrant, Postgres, Ollama) — unit tests only
- Workflow must run on GitHub-hosted runners (ubuntu-latest)
- CODEOWNERS must cover at minimum: `RAG_pipeline/`, `knowledge_base/`, `mission_control/`

## Architecture Impact
No application code changes. This is purely repository configuration:
- New files: `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/pr-validation.yml`, `CONTRIBUTING.md`
- GitHub settings: branch protection rules on `master` (configured via GitHub UI or `gh` CLI)
- GitHub Actions: PR validation workflow using GitHub-hosted runners, no self-hosted infrastructure needed

## Implementation Phases

### Phase 1: Branching strategy and contributing guide
- Define and document branch naming convention
- Write CONTRIBUTING.md with full workflow (branch, develop, test, PR, review, merge)
- Include commit message conventions and development setup

### Phase 2: PR template and CODEOWNERS
- Create `.github/PULL_REQUEST_TEMPLATE.md`
- Create `.github/CODEOWNERS` with subsystem ownership mapping

### Phase 3: PR validation CI
- Create `.github/workflows/pr-validation.yml`
- Jobs: Python syntax, RAG backend tests, RAG frontend typecheck+test, dashboard typecheck+test, MCP server tests
- Validate workflow runs successfully on a test PR

### Phase 4: Branch protection
- Configure branch protection rules on `master` via GitHub UI or `gh` CLI
- Register PR validation as required status check
- Validate by opening a test PR against the new rules

## Risks and Open Questions
- Repo is on GitHub at `joshitachu/intodata-kb-v2` -- branch protection requires admin access (confirmed)
- Existing `dev` and `production` branches exist -- branching strategy should clarify their purpose or deprecate them
- PR validation workflow may need Python and Node.js setup — check if existing tests require Docker services (Postgres, Qdrant) that won't be available on GitHub runners. May need to mock or skip integration tests.
- Solo developer self-merge: branch protection should allow this (require 0 or 1 approving reviews, not 2)

## Acceptance Criteria
- [ ] `master` branch has protection rules: require PR, require status checks, block force-push, block direct push
- [ ] `.github/CODEOWNERS` exists and maps `RAG_pipeline/`, `knowledge_base/`, `mission_control/` to owner(s)
- [ ] `.github/PULL_REQUEST_TEMPLATE.md` exists with story link, description, testing notes, checklist
- [ ] `CONTRIBUTING.md` exists at repo root covering branch naming, PR expectations, dev setup, commit conventions
- [ ] `.github/workflows/pr-validation.yml` exists and runs Python syntax, pytest, and tsc checks on PR
- [ ] PR validation workflow passes on a test PR
- [ ] PR validation is registered as a required status check in branch protection
- [ ] A test PR can be opened and validated against the new rules end-to-end
- [ ] Branch naming convention is documented and follows `feature/*`, `fix/*`, `chore/*` pattern
