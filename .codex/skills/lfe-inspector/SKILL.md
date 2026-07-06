---
name: lfe-inspector
description: Verify LFE implementation against domain truth, baselines, and tests. Use after lfe-tdd to run inspection, security/perf/complexity/dependency checks, and write .plans/inspection_report.md.
---

# LFE Inspector

Verify implementation against domain truth and validation baselines.

## Sub-Pipeline

1. `lfe-zoom-out`: orient on unfamiliar modules.
2. `lfe-inspector`: write `.plans/critique.md`, then `.plans/inspection_report.md`.
3. `lfe-diagnose`: conditional diagnosis if verification fails.

## Hard Rules

- Match documented domain knowledge exactly.
- Verify numerical outputs against baselines and snapshots.
- Run tests manually and inspect raw output.
- Primary input: `.plans/tdd_report.md`.
- Fallback: if no TDD report and unresolved protocol debt exists, verify the hotfix directly and set the report source to the protocol debt file.
- If neither input exists, halt and ask whether to escalate to `lfe-extract-domain`.

## Cycle Guard

- Cycle 1 failure: trigger `lfe-diagnose`.
- Cycle 2 failure: halt, write `status: escalated`, and present human triage.

## Sub-Checks

Run in order when available and relevant:

1. `lfe-security-check`
2. `lfe-perf-check`
3. `lfe-complexity-check`
4. `lfe-dep-audit`
5. `lfe-mutation-verify`
6. `lfe-visual-check`

Skip a check if `.plans/checks/<name>_findings.md` exists with `status: complete`.

## Output

Write `.plans/inspection_report.md` with `status: passed | failed | escalated`. Back all claims with current-session evidence.

