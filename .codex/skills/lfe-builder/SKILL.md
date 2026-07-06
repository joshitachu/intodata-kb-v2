---
name: lfe-builder
description: Execute an approved LFE active plan into production-ready code. Use after .plans/plan_critique.md has verdict PASS, or WARN with brain_confirmation, and before lfe-tdd.
---

# LFE Builder

Execute approved plans into production-ready code.

## Entry Gates

1. Parse `.plans/plan_critique.md` frontmatter.
2. Proceed only if `verdict: PASS`, or `verdict: WARN` with non-null `brain_confirmation`.
3. Otherwise halt and report the missing approval.

## Re-Entry Logic

Apply this precedence order:

1. Rework directive: if `.plans/rework_directive.md` exists and the slice matches.
2. Diagnosis retry: if `.plans/diagnosis_report.md` exists and the slice matches, skip implement/refactor and go straight to marking done.
3. Fresh implementation: default path.

## Disciplines

- Implement only features defined in `.plans/active_plan.md`.
- Centralize domain logic in designated core modules; keep I/O, UI, and framework wiring at boundaries.
- Extract configurable values to constants.
- Quote real tool output from the current session in reports.

## Output

Before handoff to `lfe-tdd`, write `.plans/builder_done.md` with YAML frontmatter and markdown including:

- Files touched.
- Plan adherence status.
- TDD hot spots.

