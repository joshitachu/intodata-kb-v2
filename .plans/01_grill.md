---
feature: Smart Runtime Log Intelligence
pipeline_step: 1_grill
date: 2026-06-17
---

## Feature

Build a smart runtime-log intelligence layer on top of the existing held runtime
log store, runtime tools, chat integration, and `/admin/logs` dashboard.

The goal is to move from "raw logs are available" to "the system can explain what
went wrong, why it likely happened, which evidence supports that answer, and what
the user should check next" without pushing large raw log volumes into the LLM
context window.

The feature should support high-volume logs, including scenarios with hundreds of
thousands or millions of log lines, by precomputing structured summaries,
incidents, error signatures, and evidence references. Chat should consume those
derived insights first and fetch raw log lines only as supporting evidence.

Logs must remain scoped to projects. For example, an ESB project has its own log
set, while another project has a different log set. Chat and dashboard views must
never mix project-scoped runtime data across authorization boundaries.

## Problem Statement

Raw runtime logs exist, but users cannot do much with them yet. The current system
can store logs, display logs, and let chat call low-level runtime tools, but the
user still has to interpret noisy records. This does not scale when a project has
large log volume.

The specific gaps:

1. Raw logs are too verbose for direct LLM use. Sending many log lines to the LLM
   creates token bloat, high latency, higher cost, and worse answers.
2. Current runtime tools expose low-level access (`get_execution_logs`,
   `detect_failures`) but not higher-level operational insight such as failed
   component, likely cause, repeated signature, timeline, or next checks.
3. The dashboard can show logs, but it does not yet surface "smart logs" where a
   user can immediately see what failed and why without waiting on or reading the
   full log stream.
4. Project scoping must stay explicit and enforced. Runtime insights, raw logs,
   and chat answers must be scoped to the authenticated user's project access.

## Definition of Done

- A seeded/mock failed Talend-style run can be analyzed end to end.
- Chat can answer questions such as "what went wrong?", "why did job X fail?",
  and "what should I check next?" using runtime log-derived insight rather than
  dumping raw log lines into context.
- Chat answers identify:
  - failed job/execution
  - failed component when detectable
  - likely cause or root-cause candidate
  - timeline of relevant events
  - evidence log lines or references
  - next checks for the user
- The dashboard shows the same smart-log insight for the test/mock failure, not
  only raw log lines.
- The approach is tested with seeded data where a failed Talend run produces a
  clear explanation such as: job X failed because component Y timed out, with
  evidence lines and recommended checks.
- High-volume behavior is designed so chat uses bounded summaries/incidents and
  evidence references, not unbounded raw logs.
- Project scoping is verified: project A users cannot see project B logs,
  incidents, summaries, evidence lines, or chat-derived answers.

## Out of Scope

- Building the real Talend TMC sync worker or any production ingestion worker.
  This feature builds the architecture and intelligence layer first; data
  acquisition comes later.
- Runtime write actions such as retry, acknowledge, resolve, rerun, or edit.
- Full trend analytics dashboards such as SLA reporting, long-term success-rate
  charts, or capacity planning.
- Replacing the existing raw log dashboard. Raw log viewing remains available as
  evidence/detail.
- Sending large raw log windows directly to the LLM as the main strategy.
- Production-grade alert routing rules. Lightweight alert/email hooks may be
  considered as optional design groundwork, but operational alerting is not the
  core first deliverable.

## Solution Rationale

The system should treat raw logs as evidence, not as the primary chat abstraction.
For large log volumes, dumping raw lines into the LLM will create token bloat and
slow, expensive, fragile answers. A better architecture is:

1. Store raw runtime logs as structured records.
2. Parse and enrich logs into structured fields such as component names, error
   codes, exception classes, retry attempts, source endpoints, and timeout values.
3. Build execution summaries and incident records from those enriched events.
4. Cluster failures by normalized error signature.
5. Give chat higher-level runtime tools such as `analyze_execution_failure`,
   `get_execution_timeline`, `find_similar_failures`, and
   `compare_with_previous_success`.
6. Let chat answer from bounded insight records first, then quote or reference a
   small number of raw log lines as evidence.

This should reduce token use, improve latency, and make the dashboard and chat
more useful than raw log browsing.

Regex/deterministic parsing is acceptable as a first layer because many Talend and
runtime log signals are structured enough to extract safely: component names,
error codes, exception names, retry counts, durations, and severity/timestamps.
LLM enrichment can be added later as an asynchronous advisory layer, but it should
not be required on the critical path for first-pass insight.

## Assumptions and Risk

- Regex and deterministic parsing will extract enough value from Talend-style logs
  to support the first version. This may be wrong if real logs vary heavily across
  customers or platforms.
- Component names from logs can be joined or at least correlated with indexed
  project documentation and generated analysis. This may require later metadata
  improvements if docs do not expose matching component names.
- High-volume log data can be reduced into bounded summaries, incident records,
  and error signatures without losing the evidence needed for trustworthy answers.
- Seeded/mock logs are representative enough to design the architecture before
  the real TMC sync worker exists.
- Alerting may become useful, but premature alert delivery could distract from
  the core requirement: reliable explanations and evidence.

## Critical Areas Touched

- RAG generation and conversational runtime tool loop:
  - Chat must prefer high-level runtime insights before raw logs.
  - Answers must remain evidence-backed and bounded.
- Auth/RBAC:
  - Runtime summaries, incidents, signatures, evidence, raw logs, dashboard views,
    and chat tools must all enforce project scope.
- Runtime API/schema/migrations:
  - New derived tables or models are likely needed for execution summaries,
    incidents, enriched events, evidence references, and error signatures.
- Frontend dashboard:
  - `/admin/logs` should surface smart summaries/incidents alongside raw lines.

## Risk if Wrong

- Users receive wrong root-cause or next-check suggestions, which makes the bot
  actively harmful during operational diagnosis.
- Cross-project log or incident data leaks through chat, dashboard, evidence
  references, or similar-failure clustering.
- Retrieval and response time become high while producing little value if the
  system still pushes too much raw log data into the LLM.
- The parser creates noisy or misleading summaries if it over-trusts weak regex
  matches.
- Users lose confidence if the system presents guesses as facts instead of
  separating evidence, likely cause, and uncertainty.
