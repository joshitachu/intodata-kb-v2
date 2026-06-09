---
feature: EP-008 Repo and Team Readiness
pipeline_step: 1_grill
date: 2026-06-09
---

## Feature
Establish repository governance for intodata-kb-v2: branch protection, branching strategy, CODEOWNERS, PR template, contributing guide, and PR validation CI pipeline via GitHub Actions. Enable structured collaboration for a team growing from 1 to 2-3 developers.

## Problem Statement
Everything goes directly to main. No branches, no CI, no GitHub Actions, no pull requests, no review process. A new developer joining would have no defined way of working -- no branch-per-feature convention, no PR template, no status checks. Even as a solo developer, pushes to main are unstructured with no conventions.

## Definition of Done
A developer can open a PR that is tested and validated against a set of rules (status checks). The PR is reviewed and accepted or rejected. Main branch is protected from direct pushes and force-pushes. Branch naming, review expectations, and ownership are documented and enforced.

## Out of Scope
- Secret scanning (GitGuardian or similar)
- Deployment pipeline (push to production/staging on merge)
- Path-based filtering (run all checks on every PR)

## Solution Rationale
Currently solo developer pushing randomly to main with no structure. Even working alone, governance provides discipline. When a second developer joins, the workflow is already in place rather than retrofitted. Standard GitHub features (branch protection, CODEOWNERS, PR templates) are well-understood and low-effort.

## Assumptions and Risk
- Repository is on GitHub (branch protection and CODEOWNERS are GitHub features)
- Team will grow to 2-3 developers in near term
- No assumptions that could be wrong identified by stakeholder

## Critical Areas Touched
None of the application critical areas (harness pipeline, MCP API routes, RAG retrieval, auth/RBAC). This is purely repository-level governance.

## Risk if Wrong
Overly restrictive branch rules could slow down solo development. Incorrect CODEOWNERS could assign wrong reviewers. Both are easily adjustable.
