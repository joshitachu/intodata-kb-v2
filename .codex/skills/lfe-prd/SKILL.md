---
name: lfe-prd
description: Transform .plans/01_grill_summary.md into a structured LFE PRD. Use in Architect phase to write .plans/02_prd.md with problem, solution, stories, decisions, tests, and exclusions.
---

# LFE PRD

Transform the grill summary into a Product Requirements Document.

## Workflow

1. Read `.plans/01_grill_summary.md` as authoritative input.
2. Explore the repository to understand current state.
3. Identify major modules to build or modify, preferring deep independently testable modules.
4. Validate module structure with the human when needed.
5. Generate `.plans/02_prd.md`.
6. Update `.plans/pipeline_status.md` if present.

## PRD Structure

- Problem Statement from user perspective.
- Solution from user perspective.
- User Stories.
- Implementation Decisions, described generically without brittle path fixation.
- Testing Decisions.
- Out of Scope.

Include YAML frontmatter with phase, step identifier, completion status, ISO timestamp, and source reference. Use domain glossary vocabulary and respect existing ADRs.

