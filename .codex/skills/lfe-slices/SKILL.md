---
name: lfe-slices
description: Decompose .plans/02_prd.md into independently demoable LFE vertical slices. Use in Architect phase to write .plans/03_slices.md and require human approval before planning implementation.
---

# LFE Slices

Convert the PRD into independently demoable vertical slices.

## Input and Output

- Read `.plans/02_prd.md`.
- Produce `.plans/03_slices.md`.
- Require human approval before progression.

## Classification

- AFK: implementable without human intervention.
- HITL: requires human decision, review, or external dependency.

Favor AFK slices where feasible.

## Discipline

Each slice should cross integration layers as a complete narrow path: schema, API, UI, and tests where applicable. A finished slice must be independently demoable or verifiable.

## Workflow

1. Draft numbered slices with title, type, blockers, and user stories.
2. Solicit feedback on granularity, dependencies, and AFK/HITL assignments.
3. Iterate until approved.
4. Save `.plans/03_slices.md` with metadata and falsifiable acceptance criteria.
5. Update `.plans/pipeline_status.md` if present.

