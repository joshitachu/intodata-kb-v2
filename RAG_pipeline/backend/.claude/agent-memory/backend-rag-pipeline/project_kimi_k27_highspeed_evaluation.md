---
name: project-kimi-k27-highspeed-evaluation
description: Measured verdict on replacing kimi-k2.5 with kimi-k2.7-code-highspeed on the RAG answer path — quality wins, but 10-15x dearer, TTFT 3x worse, and 21% truncation
metadata:
  type: project
---

Measured 2026-08-21 on 19 real production `rag_query` questions replayed against
their own captured retrieved context (provider called directly, frozen prompts).
Verdict returned to the user: **CONDITIONAL, leaning NO-GO on cost.**

Findings that cost real API spend to obtain and are NOT derivable from the code:

- **`kimi-k2.7-code-highspeed` cannot be dropped into the current provider.**
  The API rejects `thinking={"type":"disabled"}` with HTTP 400 *"only
  type=enabled is allowed for this model"*, and with thinking enabled it demands
  `temperature == 1` (*"only 1 is allowed for this model"*). The shared `kimi`
  provider in `src/generation/providers/registry.py` sends thinking-disabled at
  temperature 0.6, so selecting this model today 400s on **every** request.
  `kimi-k2.5` and `kimi-k2.6` both accept thinking-disabled at 0.6.
- **Published rates (per 1M tok):** k2.7-code-highspeed $1.90 in / $8.00 out /
  $0.38 cached-in, versus k2.5 $0.30 / $0.60. That is 6.3x input and 13.3x
  output. Measured cost per answer: **$0.0017 -> $0.0246 (14.7x dearer)**, or
  10.4x on a cold cache. Latency did fall 3.9x (42.3s -> 10.9s).
- **Thinking is ~51% of billed output** (max 93%), and thinking bills as output.
  At the provider's `max_tokens=4096`, **21% of answers (4/19) truncated
  mid-table**, all on the fast model, zero on k2.5. Worst case spent 3,816
  thinking tokens and emitted 280 visible tokens.
- **Quality itself was NOT the problem** — blind pairwise judging (judge
  `kimi-k2.6`, neither contestant, both A/B orders, 84% order-consistent) put the
  fast model ahead 68%/21%, +0.38 overall, better on grounding, citations and
  fabrication. Verified example: on a query naming a system absent from the
  retrieved context, k2.5 confabulated it 6 times while the fast model
  explicitly reported no evidence.

**Why:** the user asked whether the fast model was good enough on *quality*.
It is better than good enough; the blocker turned out to be cost and the
unsuppressible thinking budget, which is the opposite of the expected risk.

**How to apply:** do not re-run this evaluation from scratch — reuse it. If the
question returns, the open levers are the cheaper non-highspeed `kimi-k2.7-code`
route and a `max_tokens` increase; note that `max_tokens` changes were
previously rejected by the user, see [[feedback-no-unvalidated-answer-streaming]].
Model selection is a superadmin instance setting and is the user's call.
