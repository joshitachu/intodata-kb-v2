---
name: project-chat-latency-is-decode-bound
description: Measured chat latency baseline — ~99.9% is Kimi decode throughput, not retrieval; established Aug 2026, do not re-derive
metadata:
  type: project
---

Chat latency on the v2 agent harness (`conversation_agent`, cohort
`v2_effect_primary`, model `kimi/kimi-k2.5`) is decode-bound, measured 2026-08-20:

- Completed-run wall time: p50 15.1s, p90 44.0s, p99 68.8s; Kimi average ~24s.
- ~99.9% of wall time is LLM inference; `harness_agent_runs.timing.latency_ms`
  tracks `budget.elapsed_seconds` in every bucket.
- Decode throughput ~29 tok/s (518 output tokens = 17.6s). Prefill of a 10k
  prompt is only 1–3s.
- Tool execution averages 0.61s/call. Qdrant and Postgres are negligible.

**Why:** this baseline was established by direct probing and DB analysis and the
user asked that it be treated as settled rather than re-measured each session.

**How to apply:** do not propose retrieval, Qdrant, chunking or Postgres
optimizations as chat-latency fixes — they are noise at this scale. Real levers
are decode volume and provider/model, and the latter two are off-limits (see
[[feedback-no-unvalidated-answer-streaming]]). Perceived latency is the tractable
one. Re-verify with `query_usage.llm_ms` / `harness_agent_steps.latency_ms`,
which are now populated for `conversation_agent`.
