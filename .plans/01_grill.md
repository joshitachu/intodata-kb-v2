---
feature: Logs Dashboard + RAG×Runtime Cross-Reference
pipeline_step: 1_grill
date: 2026-06-16
---

## Feature
A logs dashboard page in the existing RAG frontend where users can browse live
execution data, and an LLM that cross-references that live log data with indexed
documentation in the same answer turn — so a user can ask "I see this error in the
logs, what should I look at?" and get an answer grounded in both.

## Problem Statement
Two gaps today:

1. **No UI for logs.** The only way to see runtime data is to ask the chat. There
   is no page where a user can browse executions, see failure summaries, or drill
   into log lines. The data is in Postgres but invisible.

2. **RAG and runtime tool calls never happen in the same turn.** The chat either
   does document search (Qdrant) OR runtime tool calls (Postgres). When the LLM
   produces a precomputed answer from the RAG path, it short-circuits before the
   runtime loop ever runs. A question like "I see ORA-12170 in the logs — what
   does our ESB architecture doc say I should check?" cannot be answered today.

## Definition of Done
- A `/admin/logs` page exists in the frontend. It shows a table of executions
  (filterable by project/job/state), a failures panel, and a drilldown to log
  lines for a selected execution.
- The AI correctly answers questions that require both live log context AND
  indexed documentation in the same response — verified with at least two
  representative test questions against seeded data.
- Cross-project data cannot leak: a user scoped to project A cannot receive log
  data from project B in any response, even when asking a combined question.

## Out of Scope
- Building a real polling/sync worker against Talend TMC or any live source.
  Data continues to be seeded manually. Poller is a separate future epic.
- Modifying the ingest, chunking, or embedding pipeline.
- New LLM providers or model changes.
- Write actions on the logs page (retry a job, acknowledge a failure).
- Trend/analytics aggregation (execution success rates over time) — useful but
  separate from the cross-reference story.

## Solution Rationale
**Cross-reference (LLM path):**
The agent audit found that Qdrant context is already injected into `llm_messages`
before the runtime loop fires in `conversational_rag.py`. The only thing blocking
combined answers is the `precomputed_answer` early-return in the QA branches
(lines ~1082–1102 non-streaming, ~1278–1293 streaming). Removing that early-return
and returning `llm_messages` instead is ~30–50 lines — the architecture already
supports it. This is the correct minimal change.

**Logs page:**
The frontend audit found exactly 4 missing REST endpoints on the backend
(list executions, get execution details, get execution logs, list failures). The
repository methods and DB models exist already. The admin section of the frontend
has an established table/card/hook pattern (modelled on the usage page). No new
dependencies needed.

**TMC MCP repo patterns to adopt:**
- Pre-aggregate where possible — a `detect_failures` call with a time window
  should return a grouped summary, not raw rows, to avoid filling the LLM context
  window with records it then has to count itself.
- Component-level fields (job_name, component) are the natural join key between
  log data and RAG-indexed documentation — queries like "which component failed
  and what does the doc say it does?" are feasible if RAG chunks carry job/component
  metadata.
- Consider gap-detection tools as a future category: "jobs with no documentation",
  "jobs that failed but have no matching doc" — these combine both data sources
  server-side and are more useful than raw retrieval for diagnosis.

## Assumptions and Risk
- The `precomputed_answer` short-circuit is the only fork — confirmed by the audit.
  If there are other early-returns in the non-streaming and streaming paths that
  were missed, the combined-answer feature may only partially work.
- Ollama does not support structured tool calls (`_TOOL_AWARE_FUNCS` only lists
  `kimi` today). On Ollama deployments the LLM will silently fall back to RAG-only
  even after the fix. This must be documented as a known limitation, not left silent.
- The RBAC coupling between Qdrant scope and Postgres scope is currently implicit
  (both derive from the same `project_ids` but are passed separately). If that
  coupling breaks in a future change, cross-project leakage becomes possible. The
  fix is to introduce a single `RequestScope` object that carries both, enforced
  by type — this should be done as part of this epic, not deferred.
- Seeded data is the only source right now. Test questions must work against the
  existing `esbmain` seeded data (ORA-12170 failure scenario + ESB docs if indexed).

## Critical Areas Touched
- **RAG retrieval chain** (`RAG_pipeline/backend/src/retrieval/`,
  `src/generation/conversational_rag.py`) — removing the early-return changes
  when and how Qdrant results are used. `/inspect` required.
- **Auth/RBAC** — new REST endpoints must enforce project-scope; the implicit
  RBAC coupling between RAG and runtime should be made explicit. `/inspect` required.
- **Frontend** — new admin page and 4 proxy routes. Not a critical area per
  the CLAUDE.md definition, but RBAC must be respected at the API boundary.

## Risk if Wrong
- **Wrong project data in answers**: if RBAC is not correctly threaded into the
  combined path, a user could receive log data from a project they don't have
  access to, surfaced alongside docs they do have access to. This is a data
  isolation failure.
- **Incorrect answers that cite logs as facts**: the LLM may hallucinate a
  connection between a log error and a doc section that doesn't exist. Mitigation:
  the system prompt should tell the LLM to cite which source (log vs doc) each
  claim comes from.
- **Silent RAG-only fallback on Ollama**: users on Ollama get no runtime data and
  no error — they just get an incomplete answer. Must be surfaced, not silent.
- **Streaming path inconsistency**: if only the non-streaming path is fixed and
  the streaming path is not updated symmetrically, behaviour differs between
  streaming and non-streaming clients. Both must change together.
