---
name: lfe-grill
description: Run the LFE Architect discovery interview. Use to align user intent with domain vocabulary, validate terminology, update docs, and write .plans/01_grill_summary.md.
---

# LFE Grill

Run a conversation-driven Architect phase to align intent, domain model, and terminology.

## Process

- Flag terminology conflicts with `CONTEXT.md` immediately.
- Convert vague language into precise canonical terms.
- Stress-test relationships with concrete edge cases.
- Cross-check claims against actual code and docs.
- Ask one question at a time and explore existing files before asking what the repo already answers.

## Documentation

- Update `CONTEXT.md` and `.docs/domain/glossary.md` as terms resolve, if those files exist.
- Record logic and calculations in `.docs/domain/domain-knowledge.md`, if present.
- Create ADRs only for hard-to-reverse decisions that reflect a real trade-off.
- If `CONTEXT-MAP.md` exists, document cross-context relationships.

## Output

Write `.plans/01_grill_summary.md` with:

- Resolved decisions.
- Affected modules.
- Risk zones.
- Open items.

Then mark the step complete in `.plans/pipeline_status.md` if that file exists.

