---
name: plan
description: Produce a single planning artifact before building - intent, requirements, slices, and risk in one file. Use before any change that is a new feature, a schema change, touches a critical area, or spans more than about three files.
---

# Plan

One artifact, one gate. Replaces the old grill/prd/slices/architect chain -- those
produced four files that drifted apart and were never read together.

Output: `.plans/plan.md` with `verdict: PENDING` in the frontmatter.

## Argument: $ARGUMENTS

A description of the work. If empty, ask what is being built and stop.

## 1. Interrogate before writing

Do not write the plan until the ambiguities are resolved. Ask the user about anything
where two readings would produce materially different work. Specifically:

- What is the observable outcome? How will we know it works?
- What is explicitly out of scope?
- Which existing behaviour must not change?
- What is the data contract -- shapes in, shapes out, what happens on bad input?
- Is there an existing pattern in this repo that already solves this?

Ask the questions that matter and stop. Do not ask a question whose answer you could
get by reading the code -- read the code instead.

## 2. Check the ground truth

```bash
REPO=$(git rev-parse --show-toplevel)
```

Read the actual code for every module you plan to touch. Do not plan against what
the docs claim -- this repo's docs have drifted from its code before. If a doc and
the code disagree, the code wins, and note the drift in the plan.

## 3. Write `.plans/plan.md`

```markdown
---
feature: <short name>
verdict: PENDING
created: YYYY-MM-DD
critical_area: <yes|no -- see the list in session-start>
---

# <Feature>

## Intent
What outcome the user wants, in their terms. Two or three sentences.

## Decisions
Numbered, each with the rationale. These are the answers from step 1 -- record them
so a later session does not relitigate them.

## Out of scope
Explicit. This is the field that stops scope creep.

## Design
The approach. Name the pattern being used (Strategy, Repository, Pipeline, ...) and
why it fits. If a new abstraction is introduced, justify why an existing one does not.

## Slices
Each slice independently demoable and independently committable.

| # | Slice | Files | Done when |
|---|-------|-------|-----------|

## Risks
What could break, and what existing behaviour is load-bearing near this change.

## Verification
The concrete commands that will prove this works. Not "run tests" -- the actual
invocation and the expected result.
```

## 4. Self-critique before presenting

Reread the plan against these, and fix what fails:

- Does every slice have a concrete "done when" that could be checked by someone else?
- Does the design address the root cause, or is it a narrow patch around a symptom?
- Can the same defect occur elsewhere in the codebase? If so, the plan must cover
  those sites too -- a one-off special case is not an acceptable fix here.
- Is anything in Verification untestable as written?

## 5. Present and stop

Show the plan and the open risks. Then stop.

**Do not build while `verdict: PENDING`.** The user flips it to `PASS` to open the gate.
If the user asks for changes, revise and keep `verdict: PENDING`.

Setting `verdict: PASS` yourself defeats the only gate in this workflow.
