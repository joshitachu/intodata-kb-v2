---
name: lfe-architect
description: Convert project intent into rigorous LFE plans. Use for full-pipeline changes before coding; modifies docs and planning files only and requires human approval before Builder.
---

# LFE Architect

Convert intent into structured plans. Do not edit source code.

## Pipeline

1. `lfe-grill`: establish shared understanding.
2. `lfe-prd`: synthesize requirements.
3. `lfe-slices`: create vertical slices.
4. Draft `.plans/active_plan.md` for the current slice.
5. `lfe-plan-critique`: run the pre-build gate.

## Constraints

- Modify only documentation, context, and planning files.
- Use file-based handoffs; each step reads the previous coordination file.
- Require explicit human approval before Builder work starts.

## Acceptance-Criteria Design

- Prefer widened tolerance bands over brittle exact line-count assertions.
- Validate single-file existence directly.
- Validate glob reach against expected counts.
- For doc edits, use section-boundary extraction carefully and preserve identity where needed.

## Handoff

On plan-critique PASS, mark pipeline checkboxes, set persona to Builder, and stop.

