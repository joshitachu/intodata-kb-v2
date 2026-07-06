---
name: lfe-boot
description: Bootstrap an LFE session. Use to read pipeline state, detect interruptions, load core protocol docs, handle blank canvas/domain extraction, check protocol debt, and report next skill.
---

# LFE Boot

Initialize a session from files rather than conversation memory.

## Steps

1. Read `.plans/pipeline_status.md` if present for integrity score, active persona, mission, phase, coordination files, and session count.
2. If `.plans/.session-id` exists, copy or stamp its content into `.plans/.session-booted`.
3. Detect interruptions by scanning `.plans/` coordination files:
   - Normal: resume from last completed step with user confirmation.
   - Anomaly: compare planning files against source changes to distinguish unarchived completion from interruption.
4. Read available core docs in this order:
   - `LLM_AGENT_GUIDE.md`
   - `PERSONAS.md`
   - `ASSEMBLY_LINE.md`
   - `GOVERNANCE.md`
   - `CONTEXT.md`
5. If pipeline status shows `[BLANK CANVAS]`, invoke `lfe-extract-domain`.
6. Check `.docs/quality/PROTOCOL_DEBT.md`; unresolved entries block new feature work.
7. Apply the complexity gate: full pipeline or Scout mode.
8. Report active persona, phase, session number, and next skill.

## Principles

- Documentation is authoritative.
- Keep domain logic in designated core modules.
- Use `.plans/` files for coordination state.
- Respect persona constraints.

