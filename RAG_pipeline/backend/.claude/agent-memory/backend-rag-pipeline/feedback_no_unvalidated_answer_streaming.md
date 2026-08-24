---
name: feedback-no-unvalidated-answer-streaming
description: Never stream model answer prose before the grounding validator accepts it; stream server-authored progress instead
metadata:
  type: feedback
---

Chat answer text must never reach the client before the grounding validator has
accepted it. To improve perceived latency, stream **server-authored progress**
(phase, label, elapsed time) — never model deltas, never a draft excerpt, never a
character count of the draft's content.

**Why:** the user explicitly chose this over full answer streaming. A partially
displayed answer that validation later rejects is a compliance regression, not a
UX trade-off. They pre-empted the "improvement" of streaming answer deltas and
said not to do it.

**How to apply:** `emit_answer_deltas=False` at every v2 call site is
deliberate — do not flip it. Answer text is published only after validation and
persistence. If asked to make chat feel faster, add or enrich
`assistant.progress` events, or work on real latency; do not touch the gate.

Also rejected outright in the same conversation: token trimming, changing
`max_tokens`, shrinking the grounding block, and changing model/provider
selection. Those are not available levers for latency work here.
