---
name: runtime-investigation
description: Use when the question is about live or recent runtime behaviour -- something failed, is failing, stopped running, is slow, or is suspected unhealthy. Triggers on "why did X fail", "what broke last night", "is X still running", "show me the errors", "how many runs failed this week", "did the sync work". Do NOT use for how a job is designed or what it is supposed to do, which is a documentation question, and do NOT use it to answer a design question with runtime data.
---

# Runtime investigation

The order below matters. Each step decides what the next one means, and
skipping one is how a confident, wrong diagnosis gets written.

## Procedure

1. **Did it run at all?** `nexus_read_runtime_evidence` with
   `operation=list_executions`, narrowed to the window the user implied. "It
   failed" and "it never started" look identical in a failure list and have
   completely different causes. Establish which one you are looking at first.

2. **What failed.** `nexus_read_runtime_evidence` with
   `operation=detect_failures` over the same window. Keep the window identical
   to step 1 so the two results are comparable.

3. **Is the evidence itself trustworthy?** Before concluding anything, check
   `operation=list_runtime_health` and `operation=list_runtime_sources`, and
   `operation=list_runtime_sync_status` whenever the window looks suspiciously
   quiet. A source that stopped synchronizing produces an estate that looks
   healthy because no new failures are arriving. Silence is not success.

4. **Read the error text only when you need the words.**
   `nexus_search_runtime_logs` with the exception, message fragment or symptom
   as the query -- not the user's whole sentence, which matches nothing. Leave
   the query empty to get the most recent entries for a window.

5. **Explain it last.** Once you know what failed, use documentation or
   topology to say what the failing thing was for and what depends on it. Do
   not start here.

## Counting

These reads are bounded: structured runtime evidence returns at most 50 items,
log search at most 20. **A full result set is a truncated sample, not a count.**

- Never report "there were 12 failures" from a response that came back at its
  limit. Say "at least 12 in this window", or narrow the window until the
  result is comfortably short, and state which window you used.
- The same applies to distinct job names, sources and error types derived from
  a capped list.
- If you cannot bound it honestly, say what you can bound and say what you
  cannot.

## Absence of evidence

If a project has no runtime source connected, or a source has not synchronized
recently, zero failures is not good news -- it is no news. Report it as
"nothing is being observed" and say what would need to be connected. Reporting
an unmonitored estate as healthy is the single worst outcome of this procedure.

## What to report

What is failing, since when, how often within a window you can defend, what the
evidence actually supports, and -- explicitly -- what you could not verify.
