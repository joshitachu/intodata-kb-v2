---
feature: Smart Runtime Log Intelligence
pipeline_step: 2_prd
---

## Executive Summary

IntoData already has the foundation for runtime logs: a held Postgres runtime
store, project-scoped runtime tools, chat integration, and an `/admin/logs`
dashboard. The next product step is to make logs useful at operational scale.

This feature adds a smart runtime-log intelligence layer that converts raw log
events into bounded, project-scoped insight records: enriched log fields,
execution summaries, incidents, error signatures, timelines, and evidence
references. Chat should use those records first, then fetch a small number of raw
log lines only as evidence. The dashboard should show the same derived insights
so users can see "what failed and why" without reading or waiting on raw logs.

The core design principle is: raw logs are evidence, not the primary chat
abstraction.

## Problem

Raw runtime logs exist, but they are still too low-level. The system can store log
lines, list executions, detect failures, and fetch logs, but users still have to
interpret the operational meaning themselves.

This breaks down at realistic volume. A project may have hundreds of thousands or
millions of log lines. Pushing large windows of those lines into an LLM would
create token bloat, high latency, higher cost, and unreliable answers. It also
increases the risk that the model overfits noise or presents guesses as facts.

The current runtime chat tools are useful for retrieval, but not yet for insight:
they can answer "show me the logs" but not reliably "what broke, why did it
break, has this happened before, and what should I check next?"

## Goals

1. Convert raw runtime logs into bounded, structured insight records that are
   useful for both chat and dashboard users.
2. Let chat answer operational questions from derived insights first, not from
   unbounded raw log windows.
3. Preserve raw log lines as evidence that can be quoted or referenced when
   supporting a finding.
4. Surface smart-log insight in the dashboard: failed component, likely cause,
   evidence, timeline, and next checks.
5. Keep project isolation explicit and enforced across raw logs, enriched events,
   summaries, incidents, signatures, dashboard views, and chat tools.
6. Support high-volume log data by using indexed structured queries and bounded
   records rather than vectorizing or embedding raw log lines.
7. Provide a first implementation that works end to end on seeded/mock
   Talend-style runtime data before the real TMC sync worker exists.

## Non-Goals

- Building the real Talend TMC sync worker or any production ingestion worker.
- Runtime write actions such as retry, acknowledge, resolve, rerun, or edit.
- Full operational analytics dashboards such as SLA reporting, long-term
  success-rate charts, or capacity planning.
- Replacing raw log viewing. Raw logs remain available as detail/evidence.
- Sending large raw log windows directly to the LLM as the main strategy.
- Production-grade alert routing. Optional alert/email design hooks may be
  included if cheap, but alert delivery is not the primary deliverable.
- Reworking document ingestion, embeddings, chunking, or Qdrant retrieval.

## User Stories

1. As a developer, I want to ask "why did job X fail?" and receive a short answer
   with failed component, likely cause, key evidence, and next checks, so I can
   diagnose faster than reading the full log stream.

2. As an operator, I want the logs dashboard to show a smart failure summary for
   a failed execution, so I can immediately see what matters before drilling into
   raw log lines.

3. As a developer, I want the bot to answer "has this happened before?" by
   comparing the current failure signature to prior incidents, so I can
   distinguish a new issue from a recurring one.

4. As a user working on project A, I want chat and dashboard results to include
   only project A runtime data, so unrelated customer/project logs never appear.

5. As an admin, I want bounded and explainable runtime answers, so the system does
   not waste tokens or latency by pushing excessive logs into the LLM.

6. As a future operator, I want the architecture to leave room for alerting, so
   repeated or severe incidents can later trigger email or notification flows.

## Functional Requirements

### Runtime Enrichment

1. The system must provide a deterministic enrichment step for runtime log events.
   It must extract structured fields when present:
   - component name, e.g. `tOracleConnection_1`
   - error code, e.g. `ORA-12170`
   - exception class, e.g. `SQLRecoverableException`
   - retry attempt data, e.g. `3/3`
   - duration/timeout values
   - endpoint, queue, host, database, table, or connection identifiers when safely
     available
2. Enrichment must be deterministic and testable. Regex/parser extraction is the
   first layer. LLM-generated enrichment is optional and must not be required on
   the critical path.
3. Enriched data must preserve project scope and execution id.
4. Enriched records must never store unredacted credentials or secrets.

### Execution Summary

5. The system must create or update one execution summary per runtime execution.
6. The summary must include:
   - project id
   - execution id
   - job name
   - state
   - start/end/duration
   - warning count
   - error count
   - first warning timestamp when present
   - first error timestamp when present
   - final error timestamp when present
   - detected failed component when present
   - primary error code when present
   - primary exception class when present
   - short human-readable summary
   - bounded evidence references
7. The summary must be bounded and suitable for LLM context without including a
   large raw log payload.

### Incidents and Error Signatures

8. The system must create an incident record for failed executions.
9. Incident records must include:
   - incident id
   - project id
   - execution id
   - job name
   - failed component when detectable
   - error signature
   - root-cause candidate
   - confidence or certainty level
   - symptom summary
   - first bad timestamp
   - last known healthy timestamp when derivable
   - evidence references
   - next-check suggestions
10. Error signatures must normalize repeated failures across executions. At
    minimum, a signature should combine available fields such as job name,
    component, error code, exception class, and normalized message family.
11. The system must support finding similar incidents by project scope and error
    signature.

### Evidence Model

12. Evidence references must point back to specific raw log events or bounded
    excerpts.
13. Evidence must be retrievable for dashboard and chat without exposing data
    outside the caller's project scope.
14. Chat answers must distinguish:
    - evidence facts from logs
    - inferred likely cause
    - uncertainty or missing evidence
    - recommended next checks

### Runtime Intelligence Tools

15. Add high-level runtime intelligence tools alongside the existing low-level
    tools. Initial tools should include:
    - `analyze_execution_failure(execution_id)`
    - `get_execution_timeline(execution_id)`
    - `find_similar_failures(execution_id | error_signature)`
    - `get_runtime_incident(incident_id)`
16. Existing low-level tools (`list_executions`, `detect_failures`,
    `get_execution_details`, `get_execution_logs`) must remain available for raw
    retrieval and evidence drilldown.
17. Chat tool guidance must prefer high-level insight tools for failure-analysis
    questions and use raw-log tools only for evidence/detail.
18. Tool results must be redacted, bounded, and project-scoped.

### Chat Behavior

19. Chat must answer seeded failure-analysis questions using smart-log insight
    records instead of unbounded raw log lines.
20. Chat answers for runtime failures must follow this shape:
    - finding
    - evidence
    - likely cause
    - next checks
    - uncertainty or missing data, when relevant
21. Chat must not present inferred causes as proven facts. It must label likely
    causes as likely/probable unless direct evidence proves them.
22. Chat must preserve the existing docs+logs cross-reference behavior where
    applicable: runtime insights may be combined with indexed documentation in
    the same answer.

### Dashboard Behavior

23. The `/admin/logs` page must surface smart-log insight for failed executions.
24. The failed execution drilldown must show:
    - summary
    - failed component
    - likely cause
    - evidence log lines
    - timeline
    - similar incidents count or list when available
    - raw log lines as a detail panel
25. Dashboard loading, error, and empty states must remain explicit.

### Project Scope and Authorization

26. Every new read path must derive scope from the authenticated user, not from
    query params or LLM tool arguments.
27. Optional `project_id` filters may narrow scope only. They must never widen it.
28. Cross-project incident lookup by guessed id, execution id, or signature must
    return 404 or empty results without revealing existence.

## Non-Functional Requirements

- Token efficiency: chat must not include more than a bounded summary plus a small
  evidence set for a failure-analysis answer. Raw log context must be capped.
- Latency: high-level insight reads should target sub-200ms p95 for normal
  dashboard/API reads over indexed rows.
- Scalability: data model and indexes must be suitable for high-volume logs. The
  design must avoid full-table scans for common reads by project, execution,
  incident, signature, and timestamp.
- Security: credentials/secrets must be redacted before enrichment output,
  dashboard display, or LLM context.
- Reliability: deterministic parser tests must cover the seeded Talend-style
  failure and representative non-matching log lines.
- Explainability: every runtime answer must be traceable back to evidence or
  explicitly marked as inference.
- No new dependencies unless a later plan justifies them. Prefer Python stdlib
  regex/parsing and existing SQLAlchemy/FastAPI patterns.

## Architecture Impact

### Backend Runtime Store

Add derived runtime intelligence models/tables, likely including:

- `runtime_enriched_events`
- `runtime_execution_summaries`
- `runtime_incidents`
- `runtime_error_signatures`
- optional `runtime_incident_evidence`

These should be additive migrations. Existing raw runtime tables remain the
source of truth.

### Runtime Repository and Services

Add a service layer for enrichment and summarization, likely under
`src/runtime/insights/` or `src/runtime/intelligence/`, while keeping raw store
access in `RuntimeStoreRepository`.

Expected responsibilities:

- parse/enrich raw log events
- build execution summaries
- create incident records
- compute normalized error signatures
- fetch evidence windows
- expose high-level query methods for tools and API routes

### Runtime Tool Registry

Extend `src/runtime/registry.py` and provider/tool execution with high-level
insight tools. The `stored` provider should satisfy these from Postgres-derived
insight tables. Low-level provider contracts remain intact.

### Conversational RAG

Update `agent_loop` runtime guidance so failure questions use high-level insight
tools before raw logs. Existing `RequestScope` coupling must continue to thread
the same authenticated project allow-list to docs and runtime tools.

### Runtime API

Add read-only endpoints for smart-log insights, for example:

- `GET /api/v1/runtime/executions/{execution_id}/summary`
- `GET /api/v1/runtime/incidents`
- `GET /api/v1/runtime/incidents/{incident_id}`
- `GET /api/v1/runtime/incidents/{incident_id}/evidence`
- `GET /api/v1/runtime/incidents/{incident_id}/similar`

Exact endpoint shape can be finalized in slices, but all endpoints must be
project-scoped.

### Frontend Dashboard

Extend `/admin/logs` rather than creating a separate dashboard first. The page
should show smart summaries and incidents beside raw execution/log detail.

### Auth/RBAC

This feature touches auth/RBAC because every derived artifact is project-scoped
and must be protected with the same rigor as raw logs. Similar-failure queries
are especially sensitive because signatures can accidentally reveal that another
project has the same failure.

## Implementation Phases

### Phase 1: Data Model and Deterministic Enrichment

Deliver:

- Add additive migration(s) for enriched events, execution summaries, incidents,
  signatures, and evidence references.
- Implement deterministic parser for seeded Talend-style logs.
- Add enrichment/summarization service operating on existing raw log events.
- Extend seed flow or add a developer command to build insights for seeded data.

Validation:

- Seeded rich Talend failure produces extracted component, error code,
  exception class, retry information, and evidence references.
- Parser handles non-matching lines without false positives or crashes.
- Project scope columns exist on all derived tables.

### Phase 2: Insight Repository and High-Level Tools

Deliver:

- Add repository methods for execution summary, incident detail, timeline,
  evidence, and similar failures.
- Add high-level runtime tools:
  - `analyze_execution_failure`
  - `get_execution_timeline`
  - `find_similar_failures`
  - `get_runtime_incident`
- Update runtime tool guidance to prefer insight tools for failure analysis.

Validation:

- Chat/tool tests show the model can answer "why did this fail?" using
  `analyze_execution_failure`.
- Cross-project tests prove tool arguments cannot widen scope.
- Tool payloads are bounded and do not include large raw logs.

### Phase 3: Chat Answer Quality and Evidence Discipline

Deliver:

- Update runtime answer guidance to use Finding/Evidence/Likely Cause/Next Checks.
- Ensure answers label inference and evidence separately.
- Preserve docs+logs cross-reference behavior where project docs are relevant.

Validation:

- Seeded failed Talend run -> chat explains job, component, likely cause,
  evidence lines, and next checks.
- Answer includes no large raw log dump.
- Answer does not claim certainty when only inferred evidence exists.

### Phase 4: Dashboard Smart-Log UI

Deliver:

- Extend `/admin/logs` to show execution summary and incident insight.
- Add incident/timeline/evidence panels.
- Add similar-failure count/list when available.
- Keep raw log drilldown available.

Validation:

- Dashboard displays the smart summary for seeded failure without requiring the
  user to read raw logs.
- Evidence panel links or shows bounded raw lines.
- Loading, error, and empty states render cleanly.

### Phase 5: Optional Alerting Groundwork

Deliver only if cheap and non-disruptive:

- Add fields that would later support alerting, such as severity, repeated count,
  first_seen_at, last_seen_at, and notification eligibility.
- Do not implement production email delivery unless explicitly reprioritized.

Validation:

- Incident data contains enough metadata for a future alerting slice.

## Risks and Open Questions

1. Regex may be too brittle for real customer logs. Mitigation: keep parser
   modular, deterministic, and conservative; store confidence/unknown states
   instead of overclaiming.
2. Component names may not reliably join to indexed docs. Mitigation: first
   version can correlate by component/job text; later slices may improve metadata
   or entity aliases.
3. Similar-failure clustering may leak cross-project existence if not scoped at
   every query. Mitigation: project scope is mandatory on all signature queries.
4. Incident summaries may become stale if raw logs are re-seeded or re-ingested.
   Mitigation: derived rows should be rebuildable/idempotent per project/source/
   execution.
5. Root-cause candidates may be wrong. Mitigation: answer format separates
   evidence from likely cause and includes uncertainty.
6. High-volume performance depends on indexes and bounded queries. Migration and
   repository design must be explicit about query paths.
7. Real TMC log format is not confirmed in this feature. The design must not bake
   in Talend-only assumptions beyond first parser modules.

## Acceptance Criteria

- [ ] A seeded rich Talend failure produces an execution summary with failed job,
  failed component, error code, exception class, warning/error counts, and bounded
  evidence references.
- [ ] A failed execution produces an incident with root-cause candidate, symptom
  summary, timeline fields, next checks, and an error signature.
- [ ] Chat can answer "why did this job fail?" from the incident/summary without
  dumping raw logs into the prompt.
- [ ] Chat answer includes Finding, Evidence, Likely Cause, and Next Checks.
- [ ] Chat answer references a small bounded evidence set and separates inference
  from fact.
- [ ] Dashboard shows smart failure insight for the seeded failed execution
  alongside raw log drilldown.
- [ ] Similar-failure lookup works for same-project seeded examples and does not
  return cross-project matches.
- [ ] A user scoped to project A cannot access project B summaries, incidents,
  evidence, raw logs, signatures, or chat answers.
- [ ] Parser tests cover component extraction, error code extraction, exception
  extraction, retry extraction, and safe no-match behavior.
- [ ] New API/tool payloads are bounded and redacted.
- [ ] Relevant backend tests pass, and frontend typecheck/tests pass for dashboard
  changes when those slices are implemented.
