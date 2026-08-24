---
name: answer-source-pending-backend
description: The copilot's source-honesty note reads an additive `answer_source` key the backend does not emit yet; until it does, the UI derives attribution client-side and can only guess
metadata:
  type: project
---

The docked copilot shows a per-answer attribution note (`components/copilot/AnswerSourceNote.tsx`,
derived by `topologyAnswerSource` in `lib/topologyCopilot.ts`). It prefers an additive
`structured_answer.answer_source` key with exactly four values — `topology` | `documents` |
`runtime` | `none` — read off the raw record because both members of
`MessageStructuredAnswerSchema` are `.strict()`.

As of 2026-08-21 the backend does **not** emit that key (grep of `RAG_pipeline/backend/src` found
no `answer_source`; targets flow through `harness/topology_capability.py::topology_targets`
verbatim). So the note falls back to a client-side derivation whose only positive evidence is
"the answer carried topology targets". That derivation cannot distinguish
generated-documentation answers from runtime-data answers, and cannot tell "the topology tool was
called and returned nothing" from "the topology tool was never called".

**Why:** a user drilled into the Quintiq-to-Maximo integration, asked about its implementation
design, and got an answer built from generated docs — under diagram chrome, contradicting the
diagram on screen. The note exists to make that class of mistake visible, but only the backend
knows which tool actually produced the answer.

**How to apply:** when the backend starts declaring the source, the frontend needs no change —
just verify it uses one of the four literals (anything else is ignored on purpose, so a typo
cannot claim the diagram vouched for an answer). Until then, do not present the derived
attribution as authoritative, and do not add more guessing heuristics to it. Related:
[[copilot-target-id-spaces]].
