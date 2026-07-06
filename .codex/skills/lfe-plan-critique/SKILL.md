---
name: lfe-plan-critique
description: Run the LFE pre-build five-lens critique for .plans/active_plan.md. Use before Builder writes code to produce .plans/plan_critique.md with PASS, WARN, or BLOCK.
---

# LFE Plan Critique

Stress-test `.plans/active_plan.md` before implementation.

## Revision Governance

- No `plan_critique.md`: Revision 1.
- Existing `verdict: BLOCK` and `revision: 1`: Revision 2.
- Existing `verdict: BLOCK` and `revision: 2`: halt and present human triage.

## Five Lenses

1. Falsifiability: acceptance criteria ambiguity, PRD traceability, doc-edit patterns.
2. Test strategy: realism, mock balance, side-effect clarity.
3. Domain boundaries: undocumented business logic is a BLOCK.
4. Structural impact: coupling, duplication, interface changes, scope creep.
5. Coherence simulation: mentally execute the edits and catch dangling references.

## Verdict Flow

- PASS: Builder may proceed.
- WARN: human confirmation required; set `brain_confirmation` timestamp before Builder.
- BLOCK: return to Architect; on second block, halt for triage.

## Output

Write `.plans/plan_critique.md` with frontmatter, five lens sections, and recommended actions. Do not edit source code.

