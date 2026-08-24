---
name: assistant-progress-contract
description: The assistant.progress event contract the copilot waiting state renders — phase is an OPEN set, summary carries its own elapsed suffix, and progress events never follow the first assistant.delta
metadata:
  type: project
---

The harness `assistant.progress` heartbeat is `{status, phase, summary, elapsed_ms}` with three
properties the frontend cannot see for itself:

- **`phase` is an open set.** Slugs are added whenever a capability gains a source label
  (`reading_*`, `*_project_topology`, `resolving_candidates`, ...). Never switch on a closed list
  and never attach per-phase copy — render `summary` as the user-facing text and use `phase` only
  as a grouping/animation key with a default branch.
- **`summary` is always present and already ends in its own elapsed suffix** ("<label>... 4s"), so
  rendering a separate elapsed badge beside it prints the time twice.
- **Ordering is guaranteed:** every progress event precedes the first `assistant.delta`, none
  follows it, and no answer prose ever appears in a progress payload. The first delta is therefore
  a clean handoff point from the waiting state to answer rendering.

**Why:** the backend agent that owns `src/harness/readonly_runtime.py` confirmed this contract on
2026-08-21 while making the payloads more specific; the frontend waiting state
(`components/copilot/AssistantProgress.tsx`) was built against it.

**How to apply:** when touching the copilot waiting state, the `progressDetail` field on
`StreamState`, or anything reading `assistant.progress`, treat `summary` as the display text and
`phase` as an opaque key. Related: [[answer-source-pending-backend]] for the other backend payload
the copilot depends on.

Also load-bearing for the same surface: the answer is NOT token-streamed. The harness validates the
whole answer, then splits it into chunks and fires them in one tight loop, so all deltas land within
milliseconds. Anything that looks like streaming on screen is the client-side pacing in
`hooks/useProgressiveReveal.ts`, not the wire.
