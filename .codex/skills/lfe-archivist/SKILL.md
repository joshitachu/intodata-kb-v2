---
name: lfe-archivist
description: Sync documentation and history after LFE inspection passes. Use with .plans/inspection_report.md to update docs, CHANGELOG, protocol debt, and cleanup coordination files.
---

# LFE Archivist

Keep documentation and history in sync after approved changes.

## Hard Rules

1. Maintain the project's documentation hierarchy.
2. Keep `CHANGELOG.md` at 7 milestones or fewer; move older entries to archive if the project has one.
3. Update relevant indexes for every ADR or documentation change.
4. Do not change runtime behavior.
5. Read `.plans/inspection_report.md` as the input.
6. Describe search patterns in prose where literal patterns would retrigger validation checks.

## Workflow

1. Read the inspection report and branch on `status: passed | escalated | failed`.
2. Sync docs by change type:
   - Domain terms: `CONTEXT.md` and `.docs/domain/glossary.md` if present.
   - Math or business rules: `.docs/domain/domain-knowledge.md` if present.
   - Coding standards: `.docs/architecture/engineering-standards.md` if present.
   - Architecture decisions: architecture or framework decision docs.
   - New modules: update the floor map or docs index.
3. Prepend a concise history entry to `CHANGELOG.md` when present.
4. For LFE-FORCE recoveries, mark matching `PROTOCOL_DEBT.md` entries resolved.
5. Clean coordination files:
   - More slices: remove execution files, keep planning files, set persona to Architect.
   - Mission complete: remove `.plans/` files and set mission complete state.
6. If 5 or more sessions elapsed since the last sweep, flag `lfe-hygiene`.

