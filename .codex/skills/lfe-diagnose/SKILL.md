---
name: lfe-diagnose
description: Run the LFE diagnosis loop when verification fails. Use after Inspector failure to reproduce, hypothesize, instrument, confirm root cause, apply a minimal fix, and write .plans/diagnosis_report.md.
---

# LFE Diagnose

Use when verification fails and a deterministic diagnosis is required.

## Guard Rails

- Run once per slice. If `.plans/inspection_report.md` already shows `status: failed` for the same slice, halt and escalate to human triage.
- Fix only the diagnosed problem. Do not refactor or expand scope.

## Workflow

1. Establish a reproducible failing command or scenario.
2. Run it to confirm failure.
3. Generate 3 to 5 ranked hypotheses.
4. Instrument or inspect code to test each hypothesis.
5. Confirm root cause.
6. Write the minimal fix plus a regression test.
7. Document findings in `.plans/diagnosis_report.md`.
8. Hand off to `lfe-builder` for controlled completion.

## Output

Write YAML frontmatter plus markdown recording:

- Repro command.
- Hypotheses tested.
- Root cause.
- Files touched.
- Regression test name.
- Risks for re-verification.

