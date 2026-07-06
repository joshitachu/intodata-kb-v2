---
name: lfe-tdd
description: Run the LFE Builder TDD quality gate. Use after lfe-builder to test behavior through public interfaces, iterate red-green-refactor, and write .plans/tdd_report.md.
---

# LFE TDD

Verify behavior through public interfaces. Prefer integration-style tests that read like specifications.

## Rules

- Avoid writing all tests upfront for a broad horizontal layer.
- Use vertical increments: one failing test, minimal implementation, repeat.
- Refactor only after relevant tests pass.
- Do not add speculative features.

## Cycle Checklist

- Test describes behavior, not implementation.
- Test uses public interfaces.
- Test survives internal refactoring.
- Code is minimal for the current test.

## Output

Write `.plans/tdd_report.md` with:

- Test counts.
- Behaviors tested.
- Refactors applied.
- Coverage gaps.

Back pass/fail claims with actual output from the current test run.

