---
name: project-v2-cohorts-bypass-provider-layer
description: Live chat never passes through src/generation/providers — the v2 cohorts build their own ChatOpenAI, so provider-layer accounting silently misses production
metadata:
  type: project
---

With `agent_runtime_v2_enabled=True` and the cohort percentages at 100 (the live
config), **every** chat turn runs a v2 cohort, and
`build_readonly_primary_chat_model()` in `src/harness/readonly_runtime.py` builds
its own `langchain_openai.ChatOpenAI` against Moonshot. `src/generation/providers/**`
(including `openai_compat.py`) is therefore **not** on the live chat path.

**Why:** this bit twice. Prompt-cache accounting was implemented correctly in the
provider layer and reached zero live chat turns; separately, `query_usage.llm_ms`
was populated only by the older `/query` and `conversation` interfaces. Anything
cross-cutting — cost, token accounting, caching, latency, tracing — must be
implemented (or mirrored) in `src/harness/**` or it does not exist in production.

**How to apply:** when asked to add model-level accounting or instrumentation,
check whether the change lands on `src/harness/run_budgets.py`
(`LayeredModelBudgetMiddleware`), `src/harness/model_usage.py` and
`src/harness/readonly_runtime.py` (`_accumulate_usage`). Reuse the pricing tables
in `src/generation/usage.py` (`MODEL_PRICING`, `MODEL_CACHE_READ_DISCOUNT`,
`UsageStats.calculate_cost`) rather than duplicating them into the harness.

OPEN QUESTION for the user, do not act on it: `MODEL_PRICING["kimi-k2.5"]` is
`(0.30, 0.60)` per M tokens while Moonshot publicly lists roughly $0.60 in /
$3.00 out for k2.5. If the published rate is the one actually invoiced, all
recorded Kimi cost is understated ~2x on input and ~5x on output. Needs the real
invoice to settle. See [[project-chat-latency-is-decode-bound]].
