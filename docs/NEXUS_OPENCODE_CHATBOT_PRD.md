---
title: Nexus OpenCode Chatbot
document_type: product_requirements_document
status: proposed_for_approval
version: 0.1
created: 2026-08-14
product: Nexus
scope: complete conversational runtime, tools, memory, grounding, and workflow control
runtime_decision: OpenCode-first conversational runtime
depends_on: docs/NEXUS_OPENCODE_DOCUMENTATION_AGENT_PRD.md
---

# Nexus OpenCode Chatbot — Product Requirements Document

## 1. Executive summary

Nexus will replace its custom and LangChain/LangGraph-based primary chatbot loop
with OpenCode as the coding-assistant runtime for the complete conversational
experience.

OpenCode will receive the user's message, authorized conversation context, an
optional project workspace, a per-user `MEMORY.md`, relevant attachments, and a
filtered set of Nexus tools. OpenCode will decide whether to answer normally, search
RAG, read a document, inspect source files, query runtime data, search logs, execute
a safe analytical SQL query, start a documentation Job, onboard a project, manage a
Pack, create a durable plan, pause for clarification, request approval, or finish.

Nexus will not implement a competing semantic planner or tool-selection state
machine around OpenCode. Nexus remains the deterministic control plane and source
of truth for:

- Authentication, users, organizations, and project authorization.
- Conversation and message persistence.
- Turn claims, concurrency, idempotency, and replay.
- Effective tool manifests and capability health.
- Tool schemas, policy, validation, execution, and typed observations.
- RAG retrieval, source/document access, runtime data, SQL safety, logs, connectors,
  and credentials.
- Approvals, effects, workflows, plans, task state, evidence, and receipts.
- Memory governance and compilation of each user's `MEMORY.md`.
- Grounding, citations, redaction, budgets, audit, and safe UI events.

The target operating rule is:

> OpenCode decides what the user means and which authorized tools to use. Nexus
> decides what exists, what is allowed, what is true, and whether an operation can
> execute safely.

The documentation backend defined in
`docs/NEXUS_OPENCODE_DOCUMENTATION_AGENT_PRD.md` remains a separate capability.
The chatbot does not recreate documentation generation. It starts and monitors a
Documentation Job through Nexus tools.

## 2. Decision and scope

### 2.1 Product decision

OpenCode is the proposed primary runtime for all normal Nexus chatbot turns:

- Direct conversation and guidance.
- RAG and document questions.
- Project/source investigation.
- Runtime and log questions.
- Read-only SQL-style analysis of governed runtime views.
- Multimodal questions with screenshots and attachments.
- Multi-tool investigations.
- Long-running objectives and durable plans.
- User-authorized actions, approvals, and child workflows.
- Memory-informed personalization and correction handling.

This decision is broader than using OpenCode only for the documentation backend. If
approved and promoted, it supersedes the current primary `create_agent`/
LangGraph-based conversational runtime. Existing LangGraph workflows may remain as
specialized child workflow engines invoked through Nexus capabilities; they are not
the primary conversational brain.

### 2.2 Relationship to the documentation initiative

The two initiatives have distinct responsibilities:

```text
OpenCode Chatbot
  - understands the conversation
  - answers and investigates
  - selects Nexus tools
  - starts/monitors work
  - uses per-user memory
          |
          | start_documentation_job
          v
OpenCode Documentation Agent
  - deeply analyzes a project
  - creates evidence-grounded candidate documents
  - validates, repairs, reviews, and publishes through Nexus
```

The documentation backend should be proven first because it creates the most
important complex tool the chatbot will later invoke.

### 2.3 Initial delivery boundary

The first chatbot milestone is a feature-flagged, read-only OpenCode conversation
path that supports:

- Normal tool-free conversation.
- Per-user `MEMORY.md` recall.
- Authorized RAG search and document reading.
- Project metadata.
- Structured runtime evidence.
- Bounded runtime log search.
- One guarded read-only runtime SQL tool.
- Source workspace search/read when a project source snapshot is available.
- Screenshots and attachments.
- Natural, evidence-grounded final answers with citations.

Consequential actions remain unavailable until the read-only path passes safety,
grounding, latency, and recovery gates.

## 3. Product vision

The Nexus chatbot should feel like a coding assistant connected to everything the
user is authorized to know and operate.

A user should be able to say:

- “Why did the work-order job fail last night?”
- “Here is the error screenshot. Check the logs, runtime history, and documentation.”
- “Which integrations have become slower over the last 30 days?”
- “Compare what the documentation says with what is actually deployed.”
- “Show me the failed jobs that depend on this connection.”
- “Document only the work-order subsystem.”
- “Remember that I prefer concise technical answers.”
- “That document is wrong; this process retries three times.”
- “Onboard this ZIP and tell me when it is ready.”
- “Create a migration-analysis Pack based on this requirement.”

OpenCode decides what information is needed and uses the available tools in a
natural sequence. A runtime question may require SQL and logs. An incident question
may require a screenshot, RAG, source search, and runtime evidence. A documentation
request starts the separate documentation capability. A greeting or conceptual
question may require no tools at all.

The user should not need to select “RAG mode,” “runtime mode,” “logs mode,” “agent
mode,” or a capability ID.

## 4. Current-state assessment

Nexus already has substantial chatbot foundations:

- Persistent conversations and messages.
- Shared streaming and non-streaming conversation routes.
- A capability registry with executable handler parity.
- Runtime-safe, permission-filtered capability manifests.
- Semantic tool schemas and a guarded executor.
- Typed tool observations and grounding evidence.
- Read-only, planning, and effect runtime modes.
- Conversation turn claims, idempotency, and replay protection.
- Agent Runs, Agent Steps, safe events, budgets, and trace foundations.
- Workflow Ledger, interrupts, approvals, plans, task focus, and cancellation.
- RAG retrieval, reranking, direct document reading, and grounded answer generation.
- Structured runtime evidence and bounded redacted log search.
- Dynamic request-bound connector tools, including an HTTP Elasticsearch connector.
- Project onboarding, source attachment, scanning, documentation generation, Pack
  authoring, document update, publication, and rollback capabilities.
- Image inputs on conversation turns.
- Hindsight-backed user, project, correction, and quality memory banks.

The base registry currently contains 43 built-in capabilities. Additional connector
capabilities are compiled per request according to project bindings, connector
health, Packs, and user authorization.

However, the current conversation path still owns substantial custom orchestration:

- Cohort routing between legacy, read-only, planning, and effect paths.
- Separate system prompts and runtime construction for those paths.
- Conversation-specific capability denial sets.
- A standard `create_agent` graph as the primary semantic loop.
- Special terminal handoffs for capabilities that already synthesize answers.
- Planning projections and effect-stage rules around the model loop.
- Hindsight recall that is separate from an explicit user-owned memory file.
- Fixed structured runtime operations rather than a safe flexible analytical query
  surface.

The replacement should preserve the mature Nexus control components while removing
Nexus ownership of ordinary semantic planning and tool sequencing.

## 5. Problem statement

### 5.1 User problem

The chatbot can answer documentation questions and execute several workflows, but it
can still feel like a set of routed features. Tool selection, state machinery, and
special cases can override conversational meaning. Adding a new information source
or tool can require changes across manifests, routing behavior, prompts, handlers,
and response logic.

Users want one assistant that can naturally combine documents, source, runtime
records, logs, SQL-style analysis, attachments, tools, and remembered context.

### 5.2 Architecture problem

Nexus currently implements too much of the conversational agent runtime. Maintaining
a custom loop means Nexus must solve planning, tool protocol behavior, context
management, retries, compaction, session continuity, streaming, and model-specific
behavior that a mature coding assistant already provides.

### 5.3 Tool problem

The registry has strong capability definitions, but not every registered capability
should be exposed directly to a conversational agent. Several are deliberately
runtime-hidden or low-level workflow primitives. Some current risk classifications
and semantic overlaps need correction before OpenCode receives the complete
manifest.

### 5.4 Runtime-data problem

Current structured runtime access supports a useful but fixed operation list. Users
will ask analytical questions that do not fit predefined operations, such as trends,
comparisons, aggregations, correlations, and grouped failure analysis. OpenCode needs
a safe SQL-like analytical tool without receiving database credentials or arbitrary
SQL authority.

### 5.5 Memory problem

Current Hindsight memory is useful but opaque to the coding-assistant mental model.
OpenCode works naturally with durable instruction/context files. Users need a clear,
inspectable per-user `MEMORY.md` that follows them across conversations, while Nexus
must prevent memory from becoming an ungoverned permission or misinformation layer.

## 6. Goals

### G-1: One OpenCode conversation runtime

Use one OpenCode-based runtime for ordinary dialogue, reads, investigations, plans,
actions, and workflow coordination instead of separate semantic chatbot modes.

### G-2: Tool choice by meaning

Let OpenCode choose tools based on the user's objective and tool contracts, without
production keyword routing or capability-mode selection by the user.

### G-3: Complete governed tool access

Expose every appropriate user-level Nexus capability to OpenCode while keeping raw,
low-level, or dangerous primitives behind Nexus workflows and middleware.

### G-4: Combined evidence

Allow one response to combine RAG, source files, metadata, runtime SQL, runtime
records, logs, topology, connectors, attachments, and confirmed memory.

### G-5: Per-user durable memory

Give every user an inspectable `MEMORY.md` projection that contains confirmed facts,
preferences, aliases, corrections, and durable objectives relevant to their work.

### G-6: Natural direct answers

Allow OpenCode to answer greetings, discussion, explanation, and guidance directly
without a fake response tool or mandatory workflow ceremony.

### G-7: Safe actions

Preserve Nexus policy, approvals, exact effect proposals, idempotency, workflow
receipts, and human authority for consequential operations.

### G-8: Durable conversation continuity

Allow conversations to resume across browser reconnects, backend restarts, and
OpenCode session loss without relying on an unbounded runtime transcript.

### G-9: Model flexibility

Use OpenCode's provider/model support behind Nexus certification so organizations
can choose approved hosted or local models.

### G-10: Auditable and grounded behavior

Every material factual answer and operational claim must be traceable to authorized
evidence without storing private reasoning.

## 7. Non-goals

This initiative will not:

- Give OpenCode direct production database credentials.
- Give OpenCode unrestricted SQL, shell, filesystem, or network access.
- Allow OpenCode to bypass the Nexus Capability Registry or guarded executor.
- Let OpenCode decide permissions, risk tiers, approval requirements, or publication
  authority.
- Treat `MEMORY.md` as authorization, approval, or current operational truth.
- Store raw conversations indiscriminately as long-term memory.
- Store secrets, credentials, private reasoning, or raw tool payloads in memory.
- Let source files or retrieved documents modify OpenCode's protected instructions.
- Allow multiple consequential actions to execute concurrently from one model turn.
- Make every user message create a durable plan.
- Reimplement the documentation-generation methodology inside the chatbot.
- Replace specialized durable child workflows that remain useful for onboarding,
  documentation, Pack building, or publication.
- Require users to know that OpenCode is the underlying runtime.
- Preserve every existing chatbot runtime abstraction merely for compatibility; the
  goal is a real replacement with a safe rollback window.

## 8. Canonical terminology

### OpenCode Chat Session

The runtime session used to execute one Nexus conversation. It may span multiple
turns, but it is not the durable conversation authority.

### Conversation Workspace

An isolated workspace generated for one conversation and effective scope. It
contains protected instructions, the user's `MEMORY.md`, project/source projections
when authorized, safe attachments, and a writable scratch/artifact area.

### Effective Tool Manifest

The immutable, per-turn list of OpenCode-visible tools after applying registry
health, user permissions, project scope, Pack selection, connector bindings, runtime
stage, approvals, and explicit denials.

### User Memory Record

The governed structured source from which Nexus compiles the user-specific
`MEMORY.md`. It includes provenance, scope, confirmation, status, and lifecycle
metadata.

### `MEMORY.md`

The agent-facing Markdown projection of authorized durable user memory. OpenCode can
read it but cannot mutate the authoritative memory store by editing the file.

### Runtime Analytical View

A curated, tenant-safe read-only database view exposed to the SQL Query Gateway.

### SQL Query Gateway

A Nexus service that parses, authorizes, bounds, executes, and audits read-only
analytical SQL proposed by OpenCode against curated runtime views.

### Tool Proposal

A model-proposed semantic tool and argument object. It has no effect until Nexus
validates and executes or creates an approval proposal.

### Typed Observation

The bounded, sanitized result returned by Nexus after a tool proposal is accepted,
denied, fails, needs input, or requires approval.

### Agent Run

The durable Nexus record of one conversation turn's model calls, tool proposals,
observations, usage, stop reason, and final response.

## 9. Product principles

1. **OpenCode reasons; Nexus governs.**
2. **One conversation runtime, many capabilities.**
3. **Tools are semantic product contracts, not backend endpoints.**
4. **The effective manifest is least-privilege and per turn.**
5. **Read tools return evidence; OpenCode synthesizes the answer.**
6. **Actions require receipts, not confident prose.**
7. **Memory is explicit, inspectable, correctable, and non-authoritative.**
8. **SQL is a guarded analytical language, never unrestricted database access.**
9. **Runtime loss must not lose Nexus state or repeat effects.**
10. **Natural conversation should remain natural.**
11. **No hidden chain-of-thought is required, stored, or displayed.**
12. **New connectors become tools through contracts, not chatbot code changes.**

## 10. Primary user experiences

### 10.1 Documentation question

User:

> How does the work-order integration authenticate and what happens when the API
> rejects a request?

OpenCode searches RAG, reads the most relevant authorized passages, optionally
checks source evidence when available, and answers with citations. It does not invoke
a second chatbot hidden inside the RAG tool.

### 10.2 Runtime investigation

User:

> Why did the work-order flow fail last night?

OpenCode may:

1. Resolve “last night” to the user's time zone.
2. Query structured runtime executions.
3. Use safe analytical SQL to identify failures and durations.
4. Search logs for the relevant execution IDs.
5. Search documentation for expected retry/recovery behavior.
6. Compare declared and observed behavior.
7. Return a grounded explanation with timestamps, evidence, and remaining unknowns.

### 10.3 Screenshot investigation

User uploads an error screenshot and asks what it means. Nexus scans and stages the
attachment. OpenCode inspects the image using an approved multimodal model or a
trusted extraction, then decides whether to query RAG, source, runtime, or logs.

### 10.4 Runtime trend analysis

User:

> Which production jobs became slower over the past 30 days, and are their failures
> correlated with any connections?

OpenCode generates a read-only analytical SQL query against curated runtime views.
Nexus injects project/tenant scope, validates the query AST, applies row/time/byte
limits, executes it with a read-only database role, and returns a typed table. OpenCode
may issue a second bounded query or inspect logs before answering.

### 10.5 Documentation generation

User:

> Create Deep documentation for the work-order subsystem.

OpenCode calls the high-level documentation-generation capability. Nexus creates a
separate Documentation Job governed by the documentation PRD. The chatbot monitors
the Job and links the user to its candidate review; it does not generate the files in
the chat session.

### 10.6 Memory

User:

> Remember that I prefer short technical answers and that “WO” means work order in
> this project.

OpenCode proposes two memory entries. Nexus shows or records the explicit user
confirmation, validates scope and secret safety, persists them, recompiles
`MEMORY.md`, and returns a receipt. Future conversations receive the relevant
entries.

### 10.7 Correction

User:

> No, that is wrong. The job retries three times before entering the error queue.

OpenCode treats this as a proposed correction, not immediate truth. It can inspect
documents, source, and runtime evidence. Nexus stores the statement as confirmed
memory only after the user explicitly confirms its scope/status, and it remains
distinguishable from verified current evidence.

### 10.8 Consequential action

User:

> Cancel the active onboarding workflow.

OpenCode resolves the exact workflow, proposes one cancellation tool call, and
receives an approval-required observation. Nexus persists the exact proposal. Only
an authorized user's approval executes it. OpenCode then receives the execution
receipt and explains the result.

## 11. Responsibility boundary

| Concern | OpenCode owns | Nexus owns |
|---|---|---|
| Conversation meaning | Interpret the current message and context | Preserve messages, scope, and authoritative state |
| Direct responses | Compose natural answers and guidance | Grounding/redaction validation and persistence |
| Planning | Decide whether work needs a plan and propose steps | Validate and persist durable plans; enforce dependencies/effects |
| Tool selection | Choose from the effective semantic manifest | Build/filter manifest and execute every tool |
| Multi-tool reasoning | Sequence reads and consume observations | Tool-call, time, token, byte, and cost ceilings |
| RAG | Decide queries and combine evidence | Retrieval, reranking, RBAC, chunk limits, evidence IDs |
| Source inspection | Search/read authorized workspace files | Snapshot creation, redaction, path containment, workspace scope |
| Runtime SQL | Propose analytical SQL | Parse, scope, authorize, bound, execute, and audit SQL |
| Logs/connectors | Decide when evidence is needed | Credentials, provider calls, redaction, health, result limits |
| Memory | Use `MEMORY.md`; propose updates | Authoritative store, compilation, confirmation, scope, deletion |
| Attachments | Interpret approved staged content | Upload safety, media processing, redaction, authorization |
| Actions | Propose one semantic effect | Policy, approval, idempotency, exact execution, receipts |
| Workflows | Start/monitor through tools | Durable child workflows, interrupts, cancellation, reconciliation |
| Runtime session | Native context/tool loop and compaction | Conversation/Agent Run authority and session recovery |
| UI activity | Provide runtime events | Normalize, sanitize, persist, and project user-safe events |

## 12. Target architecture

```text
Nexus web / mobile / API
          |
          v
Conversation Service
- auth and project scope
- message persistence
- turn claim/idempotency
- attachments
- Agent Run identity
          |
          v
OpenCode Chat Adapter
- pinned OpenCode service
- one scoped session per conversation
- provider/model route
- streaming and cancellation
- runtime-native context loop
          |
          +-----------------------+
          | Conversation Workspace|
          | - SYSTEM.md           |
          | - MEMORY.md           |
          | - project snapshot    |
          | - attachments         |
          | - scratch/artifacts   |
          +-----------------------+
          |
          v
Nexus MCP / Tool Gateway
- effective tool manifest
- schema and policy middleware
- approvals and effect operations
- budgets and audit
          |
          +--> RAG/search/read
          +--> source files/topology
          +--> runtime structured reads
          +--> guarded runtime SQL
          +--> logs and connectors
          +--> memory proposals
          +--> plans/tasks/workflows
          +--> documentation Jobs
          |
          v
Typed observations and evidence
          |
          v
OpenCode grounded final response
          |
          v
Nexus validation, storage, citations, and UI projection
```

## 13. OpenCode conversation runtime

### 13.1 Session identity

- Nexus creates one logical OpenCode Chat Session per Nexus conversation and
  effective authorization scope.
- The mapping includes conversation ID, user ID, organization ID, project-scope
  revision, tool-manifest hash, workspace revision, OpenCode version, and model route.
- The runtime session ID is never used as the public conversation ID.
- A project-scope or permission change invalidates the old runtime context. Nexus
  starts a replacement session or proves that the runtime can safely rebind without
  retaining unauthorized context.

### 13.2 Session durability

Nexus conversation messages, summaries, memory, plans, tasks, tool receipts,
evidence, and Agent Runs are authoritative. OpenCode session history is a runtime
cache.

If the OpenCode session disappears, Nexus creates a replacement using:

- Protected system instructions.
- Relevant recent conversation messages.
- A bounded server-generated conversation summary.
- Current `MEMORY.md`.
- Active task and plan projections.
- Relevant prior tool/evidence receipts.
- Current project/workspace/tool-manifest revision.

It must not replay an unbounded raw transcript or treat a model-generated summary as
authorization.

### 13.3 Turn lifecycle

1. Authenticate the user and resolve accessible projects.
2. Persist the user message and safe attachment references.
3. Acquire an exclusive conversation turn claim.
4. Compile the effective workspace, memory, and tool manifest.
5. Start or reconcile the OpenCode session.
6. Send the current message and authoritative context envelope.
7. Intercept every proposed tool through Nexus middleware.
8. Execute reads or create exact effect/approval proposals.
9. Return typed observations to OpenCode.
10. Validate the final response for grounding and redaction.
11. Persist the assistant response, citations, activities, run trace, usage, and
    terminal status.
12. Finalize the turn claim and stream the terminal event.

### 13.4 Direct conversation

OpenCode must be able to answer greetings, brainstorming, product discussion,
formatting requests, and ordinary guidance without calling a tool. Nexus must not
force an active task prompt, fact dump, or plan into every response.

### 13.5 Native tools

The initial Chat runtime may use OpenCode's native read, glob, grep, and bounded
workspace navigation tools. Direct writes are restricted to scratch/candidate
artifacts explicitly allowed for the conversation. General shell, unrestricted
network, and host filesystem access are denied by default.

Native tools and Nexus tools share one cumulative Job/turn budget.

## 14. Complete current tool analysis

This section inventories all 43 built-in capabilities in the current base registry
and defines their target treatment in the OpenCode Chatbot. Dynamic connector tools
are covered separately.

Disposition meanings:

- **Expose:** available directly to OpenCode when permission/scope allows.
- **Expose with approval:** model may propose; Nexus persists and controls execution.
- **Internal:** used by a high-level Nexus workflow/middleware, not model-facing.
- **Transitional:** retained during migration but not part of the target manifest.

### 14.1 Documentation and RAG capabilities

| Current capability | Current purpose | Target disposition |
|---|---|---|
| `mcp.docs.generate_project_documentation` | Start governed documentation generation | Expose as high-level `start_documentation_job`; candidate-only start does not publish |
| `mcp.docs.get_generation_status` | Read generation status | Expose |
| `mcp.docs.list_project_docs` | List generated Markdown artifacts | Internal for workflows; ordinary chat uses metadata/RAG inventory |
| `mcp.docs.read_project_doc` | Read a generated Markdown artifact | Internal for workflows; ordinary chat uses authorized RAG/document reads |
| `mcp.docs.update_from_evidence` | Apply an evidence-backed document mutation | Internal exact effect after approval/workflow validation |
| `mcp.docs.revert_evidence_update` | Revert an evidence update | Internal exact effect behind an approval-capable user-level revert tool |
| `nexus.docs.list_documentation_packs` | List available Documentation Packs | Expose |
| `nexus.workflow.propose_documentation_update` | Create an evidence-backed update workflow | Expose; publication remains approval-bound |
| `rag.docs.answer_question` | Run retrieval plus a separate RAG answer model | Transitional; remove from target OpenCode manifest after parity |
| `rag.docs.search_evidence` | Search indexed documentation passages | Expose as the primary RAG tool |
| `rag.docs.read_document` | Read bounded passages from a known authorized document | Expose |

Target change: OpenCode synthesizes the final response from RAG evidence. Nexus
reuses existing query expansion, hybrid/vector retrieval, reranking, project/group
filters, and source-card contracts, but it does not invoke a second answer-generating
chat model for the normal OpenCode path.

### 14.2 Project, source, and workflow capabilities

| Current capability | Current purpose | Target disposition |
|---|---|---|
| `mcp.projects.attach_git_source` | Attach a Git repository | Internal step of the governed onboarding workflow |
| `mcp.projects.attach_uploaded_zip_source` | Attach a verified uploaded ZIP | Internal step of onboarding; raw upload bytes never become tool arguments |
| `mcp.projects.list_active_workflows` | List active onboarding/scan/doc workflows | Expose |
| `mcp.projects.list_source_options` | Inspect low-level source choices | Internal; high-level onboarding asks focused user questions |
| `mcp.projects.publish_index` | Push documentation to search | Internal exact workflow effect with approval/version checks |
| `mcp.projects.read_status` | Read low-level onboarding status | Internal or folded into a high-level project/workflow status tool |
| `mcp.projects.trigger_scan` | Trigger the project scanner | Internal child step of onboarding/reingestion |
| `nexus.workflow.onboard_project` | Start governed project onboarding | Expose; exact scope/source confirmation required |
| `nexus.workflow.resume_onboarding` | Resume interrupted onboarding | Expose for the exact owner-scoped workflow |
| `nexus.workflows.cancel` | Cancel an active workflow | Expose with approval or explicit exact confirmation; execute one workflow only |

### 14.3 Agent plan and conversation-task capabilities

| Current capability | Current purpose | Target disposition |
|---|---|---|
| `nexus.agent_plan.project_objective` | Determine whether a topology needs a durable plan | Internal validation/middleware; OpenCode decides whether to propose a plan |
| `nexus.agent_plan.create_agent_plan` | Create a durable plan | Expose for genuinely multi-step objectives |
| `nexus.agent_plan.read_agent_plan` | Read current plan | Expose and correct its risk classification to read-only |
| `nexus.agent_plan.revise_agent_plan` | Revise a versioned plan | Expose |
| `nexus.agent_plan.pause_agent_plan` | Pause a plan | Expose |
| `nexus.agent_plan.resume_agent_plan` | Resume a plan | Expose |
| `nexus.agent_plan.abandon_agent_plan` | Abandon a plan | Expose with exact version/owner validation |
| `nexus.conversation_tasks.background_conversation_task` | Move a task out of conversational focus | Expose |
| `nexus.conversation_tasks.pause_conversation_task` | Pause task prompting without cancelling domain work | Expose |
| `nexus.conversation_tasks.resume_conversation_task` | Return a task to focus | Expose |

OpenCode's private working plan is not durable product state. When the user needs a
multi-turn, restart-safe objective, OpenCode must use the Nexus Agent Plan tools.

### 14.4 Pack capabilities

| Current capability | Current purpose | Target disposition |
|---|---|---|
| `nexus.packs.create` | Create and save a complete private Analysis Pack | Expose as the preferred high-level Pack action |
| `nexus.packs.drafts.create` | Create low-level draft state | Internal to the Pack workflow |
| `nexus.packs.drafts.read` | Read an owned draft | Expose when a Pack task is active |
| `nexus.packs.drafts.configure` | Configure user-facing Pack fields | Expose when a Pack task is active |
| `nexus.packs.drafts.mutate` | Apply low-level declarative draft patches | Internal to the Pack workflow |
| `nexus.packs.drafts.validate` | Validate a draft | Expose |
| `nexus.packs.drafts.save` | Save an immutable Pack version | Expose with approval |
| `nexus.packs.drafts.abandon` | Abandon a draft | Expose with approval |

The chatbot must prefer high-level semantic actions. It must not use hidden draft
mutation primitives merely because their schemas are available internally.

### 14.5 General, metadata, runtime, and log capabilities

| Current capability | Current purpose | Target disposition |
|---|---|---|
| `nexus.conversations.read_summary_facts` | Read bounded facts from the current conversation | Expose only when older facts are not already in the supplied bounded context |
| `nexus.metadata.query` | Query projects, documents, system overview, and profile metadata | Expose |
| `runtime.stored.query_evidence` | Execute fixed structured runtime operations | Expose for common safe runtime reads |
| `runtime.logs.search_evidence` | Search bounded redacted logs | Expose |

### 14.6 Dynamic connector tools

Nexus currently compiles project-bound connector instances into request-specific
capabilities. Existing provider types include deterministic fixture knowledge and a
read-only allowlisted HTTP Elasticsearch connector.

OpenCode may receive these tools only when:

- The connector instance is enabled and healthy.
- It is bound to an authorized project in the effective scope.
- The user's permissions and effective Packs allow the capability.
- Nexus owns credentials and performs the provider call.
- Results are bounded, sanitized, evidence-typed, and audited.

Adding a new connector must require a provider definition, semantic schema, handler,
health behavior, auth mode, risk class, budgets, and tests—not chatbot routing code.

## 15. Target OpenCode tool surface

### 15.1 Read tools

- Search indexed documentation.
- Read bounded passages from a known document.
- Query project/document/system metadata.
- Read bounded conversation facts.
- Read the effective user memory projection and memory entry metadata.
- Search project source with native workspace tools.
- Query topology and source/runtime correlations.
- Read structured runtime evidence.
- Execute guarded analytical runtime SQL.
- Search bounded redacted runtime logs.
- Search configured external connectors such as Elasticsearch.
- Read active workflow and documentation Job status.
- List available Documentation and Domain Packs.

### 15.2 Planning and task-control tools

- Create/read/revise/pause/resume/abandon an Agent Plan.
- Background/pause/resume a conversation task.
- Request one focused clarification.

### 15.3 High-level workflow/effect tools

- Start a documentation Job.
- Propose a document update or revert.
- Start/resume project onboarding.
- Cancel an exact workflow.
- Create/configure/validate/save/abandon a Pack.
- Propose, confirm, supersede, or delete user memory.

### 15.4 Never model-facing

- Raw database connections or credentials.
- Raw Qdrant access.
- Low-level source attachment or scanner triggers.
- Direct publication/indexing.
- Direct document filesystem mutation.
- Internal Pack draft mutation primitives.
- Policy, approval, task-token, turn-claim, or receipt mutation.
- Audit database writes.
- Unfiltered OpenCode runtime/config/provider inspection endpoints.

## 16. RAG integration

### 16.1 Target behavior

RAG becomes a set of retrieval tools used by OpenCode, not a second chatbot.

```text
User question
  -> OpenCode decides retrieval is needed
  -> Nexus RAG search/query-expansion/reranking
  -> bounded evidence chunks and source metadata
  -> optional focused document read
  -> OpenCode combines RAG with other evidence
  -> grounded final response with Nexus citations
```

### 16.2 Retrieval requirements

- Preserve document group visibility and project RBAC.
- Canonicalize project identifiers server-side.
- Reuse configured dense, hybrid, expanded-query, and reranked retrieval.
- Return stable evidence IDs, document IDs, chunk IDs, filenames, project IDs,
  relevance scores, and bounded text.
- Support query, project filters, document filters, roles/layers, top-k, and safe
  time/version filters where applicable.
- Never return unauthorized chunks and rely on OpenCode to filter them.
- Distinguish no evidence, denied evidence, unavailable retrieval, and conflict.

### 16.3 Synthesis requirements

- OpenCode writes the final answer for the target path.
- Material document claims cite returned evidence IDs.
- Unused retrieved content must not appear automatically.
- The legacy `rag.docs.answer_question` terminal-answer tool remains only during
  migration and is removed from the final OpenCode manifest after quality parity.
- A fallback answer model may exist for explicit `/query` API compatibility, but it
  must not become an invisible second synthesis step in OpenCode chat.

## 17. Runtime SQL Query Gateway

### 17.1 Product requirement

OpenCode needs flexible SQL-style analysis of runtime data, but never direct SQL
authority over Nexus's operational database.

The target tool is conceptually:

```text
runtime.analytics.query_sql
```

Input:

- SQL text or a typed analytical query plan.
- Authorized project scope selected by Nexus, not trusted from SQL text.
- Optional requested time window.
- Requested row limit within server maximum.

Output:

- Typed column definitions.
- Bounded result rows.
- Truncation and timing metadata.
- Scope/time qualifications.
- Stable evidence/receipt ID.
- Safe validation or execution error.

### 17.2 Curated runtime views

The gateway may expose read-only views such as:

- `runtime_jobs`
- `runtime_executions`
- `runtime_failures`
- `runtime_sources`
- `runtime_health`
- `runtime_sync_status`
- `runtime_run_metrics`
- `runtime_connections`
- `runtime_job_dependencies`
- `runtime_incidents`
- `runtime_static_correlations`

Names are logical contracts and need not reveal internal database table names.

### 17.3 SQL safety requirements

- Execute through a separate read-only database role or read replica.
- Parse SQL into an AST; do not rely on regex-only filtering.
- Accept exactly one `SELECT` statement or supported `WITH ... SELECT` statement.
- Reject DDL, DML, transaction control, session changes, stored procedures,
  extensions, file/network functions, advisory locks, system catalog access, and
  volatile/unsafe functions.
- Allow only certified views and columns.
- Inject organization/project predicates outside model control.
- Enforce a bounded time window for large runtime tables.
- Enforce server row, byte, join, complexity, cost, and statement-time limits.
- Apply an outer server-owned result limit even if the SQL contains a larger limit.
- Prevent comments or encoding tricks from bypassing validation.
- Redact sensitive columns and log text.
- Record query hash, normalized AST shape, scope, views, timing, row count, and
  outcome without exposing secrets.
- Return typed feedback that lets OpenCode correct a malformed analytical query once
  or replan using structured runtime tools.

### 17.4 Default ceilings

Initial defaults should be conservative and configurable:

- Maximum 200 returned rows.
- Maximum 256 KiB serialized result.
- Maximum five joined certified views.
- Maximum 31-day time range unless policy explicitly allows more.
- Five-second statement timeout.
- At most two SQL calls in one ordinary chat turn.

These are safety ceilings, not quality targets, and must be tuned from measured
workloads.

### 17.5 Structured tools remain

The current fixed `runtime.stored.query_evidence` operations remain the preferred
choice for common inventory, health, sync, execution, and failure questions. SQL is
for comparisons, aggregations, trends, correlations, and questions the structured
operations cannot express cleanly.

## 18. Per-user `MEMORY.md`

### 18.1 User-visible behavior

Every active user has a governed memory record that Nexus compiles into one
OpenCode-readable Markdown file for each conversation:

```text
/nexus-context/user/MEMORY.md
```

The file follows the user across conversations and devices. Users can inspect and
manage its contents through Nexus.

### 18.2 File structure

Example projection:

```markdown
---
schema_version: 1
memory_revision: 18
generated_at: 2026-08-14T15:00:00Z
---

# User preferences

- [mem-pref-12] Prefer concise technical answers.

# Terminology and aliases

- [mem-alias-7] In project `werkorder`, “WO” means “work order”.

# Confirmed corrections

- [mem-correction-4] The work-order process retries three times before the
  error queue. Status: user-confirmed; current-source verification pending.

# Durable objectives

- [mem-objective-2] Modernize work-order documentation. Status: active.

# Important past outcomes

- [mem-outcome-9] A Deep documentation candidate was approved on 2026-08-10.
```

The runtime projection must exclude internal user IDs, credentials, private admin
metadata, unauthorized project memory, raw conversations, and deleted/superseded
entries.

### 18.3 Authoritative storage

The required agent contract is a per-user `MEMORY.md`, but a mutable container-local
file is not sufficient as authoritative multi-user storage. Nexus must persist
structured memory records in a durable, tenant-aware store and compile the Markdown
projection deterministically.

Hindsight may remain a semantic recall/index layer, but it does not independently
authorize or promote memories. The structured Nexus memory record owns confirmation,
scope, status, provenance, retention, supersession, and deletion. Hindsight and
`MEMORY.md` are projections/consumers of that record.

### 18.4 Memory categories

- User response and formatting preferences.
- Stable terminology and aliases.
- Project interests and defaults.
- Confirmed factual corrections with verification status.
- Explicit durable goals and objectives.
- Important completed workflow outcomes.
- User-approved reusable procedures.

Do not retain:

- Greetings or casual conversation.
- Every question and answer.
- Guessed personality traits.
- Credentials, tokens, secrets, or private payloads.
- Temporary runtime status.
- Unconfirmed claims stated by the assistant.
- Information from a project the user can no longer access.
- Hidden reasoning or prompt content.

### 18.5 Memory write protocol

OpenCode never changes authoritative memory by editing `MEMORY.md`.

1. OpenCode calls `propose_memory_update` with category, scoped statement, rationale,
   and optional evidence IDs.
2. Nexus validates ownership, scope, content, conflicts, duplication, and secret
   safety.
3. Pure user preferences explicitly requested with “remember” may be accepted
   immediately under policy.
4. Factual corrections require explicit confirmation and retain verification status.
5. Deletion, cross-project promotion, and organization-wide memory require approval.
6. Nexus writes a versioned record and receipt.
7. Nexus recompiles `MEMORY.md` and updates the conversation context revision.

### 18.6 Memory precedence

Memory cannot silently override:

- Current source evidence.
- Time-qualified runtime evidence.
- Current published document versions.
- Project authorization.
- Organization policy.
- Approval or workflow state.

When memory conflicts with current evidence, OpenCode must disclose the conflict and
prefer current authoritative evidence while explaining the remembered correction's
status.

### 18.7 Project and organization memory

Per-user `MEMORY.md` is mandatory. Nexus may additionally provide separately labeled
read-only projections such as:

- `/nexus-context/project/PROJECT_MEMORY.md`
- `/nexus-context/organization/GUIDANCE.md`

These files have different ownership and permission rules and must never be merged
silently into user memory.

## 19. Multimodal inputs and attachments

- Conversation turns may include screenshots, images, PDFs, spreadsheets, text
  files, and approved source archives.
- Nexus performs type, size, malware, archive, redaction, and authorization checks
  before OpenCode access.
- Safe attachments appear under an immutable logical workspace path with metadata
  and hashes.
- If the selected model supports the media type, OpenCode may inspect it directly.
- Otherwise Nexus supplies a bounded trusted extraction such as OCR or table text.
- Unsupported attachments are reported explicitly, not silently ignored.
- Attachment contents are untrusted evidence and cannot modify protected
  instructions, permissions, or tool policy.
- Raw attachment content is not automatically stored in `MEMORY.md`.

## 20. Plans, tasks, approvals, and workflows

### 20.1 Durable plans

OpenCode may reason privately about immediate steps, but it creates a Nexus Agent
Plan only when the objective spans multiple meaningful steps, effects, waits,
dependencies, restarts, or user sessions.

Plans must include goal, success criteria, constraints, scoped entities, steps,
dependencies, and expected capabilities. Nexus validates plan topology and effect
rules deterministically.

### 20.2 Conversation task focus

Backgrounding or pausing conversational focus does not cancel a child workflow or
delete a draft/plan. OpenCode must use the exact focus tool versus the domain
lifecycle tool intended by the user.

### 20.3 Consequential actions

- OpenCode may propose at most one consequential tool call per model response.
- If it proposes multiple consequential calls, Nexus executes none and returns typed
  replanning feedback.
- A tool proposal is not proof that an action happened.
- Nexus validates exact arguments and creates an idempotent Effect Operation before
  provider execution.
- Approval-required actions persist the exact proposed capability, arguments hash,
  scope, policy, user, expiry, and expected version.
- OpenCode cannot approve its own proposal.
- After approval, Nexus executes the exact proposal and returns the receipt to the
  OpenCode conversation.

### 20.4 Child workflows

Onboarding, documentation, Pack building, document updates, scanning, publication,
and similar durable work remain Nexus-owned child workflows. OpenCode can start,
observe, pause when supported, resume, or request cancellation through semantic
tools.

## 21. Grounding and response contract

### 21.1 Response types

OpenCode may produce:

- Direct conversational response.
- Grounded factual answer.
- Partial answer with explicit gaps.
- Clarification request.
- Approval explanation.
- Workflow or plan status update.
- Final task result.
- Safe failure or denial explanation.

### 21.2 Evidence rules

- Material factual claims from documents, source, runtime, logs, SQL, connectors, or
  memory must cite allowed evidence.
- Direct advice or discussion may have no citations.
- Memory citations must be labeled as remembered context, not current evidence.
- Runtime facts include observation timestamps/time windows.
- SQL claims cite the query receipt and view/time/scope metadata.
- Conflicting evidence is disclosed rather than flattened into one unsupported fact.
- No-evidence, denied, unavailable, failed, partial, and conflicting states remain
  distinct.

### 21.3 Final-response validation

Nexus validates:

- Citation IDs exist and were returned in the current authorized run/context.
- Material claims have eligible evidence classes.
- Secrets and unsafe payload fragments are absent.
- Tool outcomes are not overstated.
- Pending approvals/workflows are not described as complete.
- Memory is not presented as permission or current operational truth.
- SQL results are not generalized beyond their scope/time window.

One bounded correction may return structured findings to OpenCode. Repeated failure
produces a concise safe response and retains the diagnostic trace.

## 22. Functional requirements

Requirements use MUST, SHOULD, and MAY as normative terms.

### 22.1 Conversation runtime

- **CHAT-FR-001:** Every public chat turn MUST use OpenCode after final promotion;
  no semantic keyword/mode router may choose a different conversational runtime.
- **CHAT-FR-002:** OpenCode MUST be able to return a normal assistant response
  without calling a synthetic response tool.
- **CHAT-FR-003:** OpenCode MUST receive recent conversation context, relevant durable
  summaries, current `MEMORY.md`, authorized project context, and the effective tool
  manifest.
- **CHAT-FR-004:** Nexus MUST persist the user message before provider execution.
- **CHAT-FR-005:** Every turn MUST have one durable Agent Run and exclusive turn
  claim.
- **CHAT-FR-006:** Replayed client requests MUST return the prior result or current
  in-progress status without duplicate provider/tool/effect work.
- **CHAT-FR-007:** One conversation MUST NOT execute two active turns concurrently
  unless an explicit future concurrency protocol is introduced.
- **CHAT-FR-008:** OpenCode session identity MUST be scoped to user, conversation,
  organization, project-context revision, and manifest revision.
- **CHAT-FR-009:** Runtime loss MUST be recoverable from Nexus state without trusting
  an unbounded transcript.

### 22.2 Tool manifest and selection

- **CHAT-FR-010:** Nexus MUST compile an immutable effective tool manifest for every
  turn.
- **CHAT-FR-011:** Tool visibility MUST be filtered by health, permissions, project
  scope, Packs, connector bindings, runtime stage, and explicit policy.
- **CHAT-FR-012:** OpenCode MUST choose tools by semantic meaning; production routing
  MUST NOT depend on matching user-message keywords.
- **CHAT-FR-013:** Every model-facing tool MUST have a stable semantic name, complete
  JSON Schema, purpose, use/non-use guidance, preconditions, risk class, output
  contract, approval behavior, and recovery guidance.
- **CHAT-FR-014:** Model-facing and server-enforced schemas MUST derive from the same
  authoritative capability definition.
- **CHAT-FR-015:** OpenCode-specific tool names MUST map bijectively to canonical
  Nexus capability IDs and fail on collisions.
- **CHAT-FR-016:** Runtime-hidden and internal-only capabilities MUST never enter the
  OpenCode manifest.
- **CHAT-FR-017:** Tool availability changes MUST be explained through safe typed
  state rather than allowing OpenCode to invent missing capabilities.
- **CHAT-FR-018:** Adding a connector capability MUST not require editing chatbot
  routing logic.

### 22.3 Tool execution

- **CHAT-FR-020:** Every Nexus tool call MUST pass through policy, schema validation,
  scope validation, budgets, idempotency, execution, observation sanitization, and
  audit.
- **CHAT-FR-021:** Unknown, malformed, unavailable, unauthorized, or out-of-scope
  calls MUST execute no handler.
- **CHAT-FR-022:** Operation-ID reuse with different arguments MUST be rejected.
- **CHAT-FR-023:** Tool observations MUST be bounded, typed, sanitized, and usable by
  OpenCode for correction or replanning.
- **CHAT-FR-024:** Read-tool failure MAY be retried only when typed retry guidance and
  budgets permit.
- **CHAT-FR-025:** Consequential proposals MUST execute sequentially, at most one per
  model response.
- **CHAT-FR-026:** Multiple consequential proposals in one response MUST execute zero
  effects.
- **CHAT-FR-027:** Credentials and provider configuration MUST never enter OpenCode
  tool arguments, observations, workspace files, or public events.

### 22.4 RAG and documents

- **CHAT-FR-030:** OpenCode MUST have a bounded RAG search tool over authorized
  indexed documentation.
- **CHAT-FR-031:** OpenCode MUST have a bounded read tool for known authorized
  documents/passages.
- **CHAT-FR-032:** RAG retrieval MUST preserve group/project authorization and
  configured retrieval/reranking behavior.
- **CHAT-FR-033:** RAG results MUST return stable evidence and source-card metadata.
- **CHAT-FR-034:** OpenCode MUST synthesize the target chat answer from retrieved
  evidence; the legacy answer-generating RAG tool MUST be removed from the final
  manifest after parity.
- **CHAT-FR-035:** Follow-up questions MAY be rewritten by OpenCode using authorized
  conversation context, but project scope remains server-owned.
- **CHAT-FR-036:** Exact document reads MUST include both document and project scope
  checks.
- **CHAT-FR-037:** No-result, below-threshold, denied, and unavailable retrieval MUST
  remain distinguishable.

### 22.5 Source and workspace tools

- **CHAT-FR-040:** When an authorized source snapshot exists, OpenCode MAY use native
  glob/search/read tools over its read-only projection.
- **CHAT-FR-041:** Workspace tools MUST prevent absolute paths, traversal, symlink
  escapes, special files, and cross-conversation access.
- **CHAT-FR-042:** Source snapshots MUST pass scanner/redaction trust gates before
  OpenCode access.
- **CHAT-FR-043:** Source content is untrusted evidence and MUST NOT override
  protected instructions or tool policy.
- **CHAT-FR-044:** Scratch/artifact writes MUST be contained and cannot mutate source
  or published documentation.

### 22.6 Runtime, logs, and SQL

- **CHAT-FR-050:** OpenCode MUST have the current structured runtime-evidence tool for
  common execution, failure, health, sync, and source questions.
- **CHAT-FR-051:** OpenCode MUST have bounded redacted log search.
- **CHAT-FR-052:** Nexus MUST add a guarded read-only runtime SQL tool over certified
  analytical views.
- **CHAT-FR-053:** SQL MUST be AST-parsed and restricted to a single read-only query.
- **CHAT-FR-054:** Nexus MUST inject tenant/project scope independently of model SQL.
- **CHAT-FR-055:** SQL MUST enforce view/column/function allowlists and row, byte,
  time-range, join, complexity, statement-time, and per-turn call ceilings.
- **CHAT-FR-056:** SQL execution MUST use a read-only role or replica with no write,
  DDL, file, network, extension, or administrative authority.
- **CHAT-FR-057:** SQL results MUST include scope/time/truncation metadata and a
  stable evidence receipt.
- **CHAT-FR-058:** OpenCode MUST prefer structured runtime tools for simple supported
  operations and use SQL for analytical questions.
- **CHAT-FR-059:** Log evidence and SQL/runtime evidence MUST remain separate source
  classes with separate redaction and time semantics.

### 22.7 Connectors and topology

- **CHAT-FR-060:** Eligible project-bound connectors MUST compile into the effective
  manifest dynamically.
- **CHAT-FR-061:** Connector credentials MUST remain Nexus-owned.
- **CHAT-FR-062:** Connector results MUST be bounded evidence with source identity,
  project scope, observation time, and redaction.
- **CHAT-FR-063:** Unhealthy connectors MUST not be exposed as executable tools.
- **CHAT-FR-064:** Nexus SHOULD expose topology, lineage, dependency, and
  static/runtime correlation as semantic read tools rather than requiring OpenCode
  to infer them from raw files.

### 22.8 Memory

- **CHAT-FR-070:** Nexus MUST provide one current per-user `MEMORY.md` projection to
  OpenCode for each conversation.
- **CHAT-FR-071:** `MEMORY.md` MUST be derived from durable structured records with a
  revision and generation timestamp.
- **CHAT-FR-072:** OpenCode MUST have read-only access to the file and MUST use memory
  tools for mutation.
- **CHAT-FR-073:** Memory writes MUST be scoped, versioned, provenance-bearing,
  secret-safe, and idempotent.
- **CHAT-FR-074:** Explicit user preferences requested with “remember” MAY be stored
  immediately when policy permits; factual corrections require confirmation/status.
- **CHAT-FR-075:** Memory MUST NOT grant access, approval, or workflow truth.
- **CHAT-FR-076:** Current authoritative evidence MUST outrank conflicting memory.
- **CHAT-FR-077:** Users MUST be able to inspect, search, confirm, supersede, and
  delete their memory entries.
- **CHAT-FR-078:** Losing project access MUST remove that project's entries from
  future user memory projections.
- **CHAT-FR-079:** Hindsight MAY index and recall memory but MUST NOT independently
  promote unconfirmed content into `MEMORY.md`.

### 22.9 Images and attachments

- **CHAT-FR-080:** Conversation turns MUST support safe screenshots and image
  attachments.
- **CHAT-FR-081:** Attachments MUST pass type, size, malware, redaction, and scope
  checks before OpenCode access.
- **CHAT-FR-082:** The selected model route MUST declare supported input media.
- **CHAT-FR-083:** Unsupported media MUST produce a trusted extraction or a clear
  capability limitation.
- **CHAT-FR-084:** Attachments MUST use immutable content hashes and contained logical
  paths.
- **CHAT-FR-085:** Attachment content MUST NOT automatically enter long-term memory.

### 22.10 Plans and tasks

- **CHAT-FR-090:** OpenCode MAY create a durable Nexus Agent Plan only when the
  objective genuinely spans multiple steps, waits, dependencies, effects, or turns.
- **CHAT-FR-091:** Direct answers and simple tool sequences MUST NOT require a durable
  plan.
- **CHAT-FR-092:** Nexus MUST validate plan topology, versions, effect references,
  dependencies, and ownership.
- **CHAT-FR-093:** `read_agent_plan` MUST be classified and exposed as read-only.
- **CHAT-FR-094:** Task focus controls MUST remain distinct from workflow
  cancellation and plan/draft abandonment.
- **CHAT-FR-095:** OpenCode MUST receive bounded active-plan/task context without an
  active task forcing itself into unrelated replies.

### 22.11 Actions, approvals, and workflows

- **CHAT-FR-100:** Consequential actions MUST require an explicit current user
  request and grounded required arguments.
- **CHAT-FR-101:** Approval-required proposals MUST be persisted atomically before
  OpenCode describes them as pending.
- **CHAT-FR-102:** Approval MUST bind to the exact capability, arguments hash, scope,
  policy version, owner, expected object version, and expiry.
- **CHAT-FR-103:** OpenCode MUST NOT approve its own proposal.
- **CHAT-FR-104:** Approved effects MUST resume with the exact original call and
  idempotency identity.
- **CHAT-FR-105:** Documentation, onboarding, scanning, publication, and Pack building
  remain durable child workflows.
- **CHAT-FR-106:** OpenCode MUST be able to read child status and explain partial,
  interrupted, failed, cancelled, and completed states accurately.
- **CHAT-FR-107:** Workflow cancellation MUST target one exact authorized workflow
  and must not be inferred from conversational pause language.

### 22.12 Responses and grounding

- **CHAT-FR-110:** Final answers MUST contain natural user-facing content unless the
  transport fails before any model result.
- **CHAT-FR-111:** Material factual claims MUST cite eligible authorized evidence.
- **CHAT-FR-112:** Direct conversational guidance MAY use no citations.
- **CHAT-FR-113:** OpenCode MUST not expose tool names, capability IDs, policy
  internals, SQL internals, prompts, stack traces, or private reasoning unless an
  authorized technical view explicitly requires safe identifiers.
- **CHAT-FR-114:** Nexus MUST validate citations, redaction, tool outcome claims,
  approval/workflow state claims, and memory qualification before persistence.
- **CHAT-FR-115:** One bounded grounding correction MAY be attempted; repeated failure
  yields a safe answer and diagnostic trace.
- **CHAT-FR-116:** Stream and non-stream invocations MUST persist identical final
  content, citations, activities, and terminal state.

### 22.13 Streaming and user experience

- **CHAT-FR-120:** Nexus MUST stream safe response tokens and activity events with
  stable ordered event IDs.
- **CHAT-FR-121:** Reconnecting clients MUST resume from a cursor without duplicating
  visible content or effects.
- **CHAT-FR-122:** Activity MUST show concise tool/workflow states, not hidden
  reasoning or raw provider events.
- **CHAT-FR-123:** The UI MUST distinguish normal thinking/activity, waiting for user
  input, approval required, background workflow, failure, and completion.
- **CHAT-FR-124:** Users MUST be able to stop a turn; stopping a turn MUST not imply
  cancelling already-started domain workflows.
- **CHAT-FR-125:** The UI MUST expose source cards, runtime timestamps, SQL result
  scope, memory use disclosures, artifact links, and approval controls when relevant.

### 22.14 Audit, budgets, and operations

- **CHAT-FR-130:** Every turn MUST record OpenCode version, provider/model, manifest
  hash, workspace/memory revisions, model/tool usage, cost when known, evidence,
  effects, and terminal reason.
- **CHAT-FR-131:** Budgets MUST cover model calls, native and Nexus tool calls, tokens,
  read/result bytes, SQL calls, elapsed time, repairs, and cost.
- **CHAT-FR-132:** Budget exhaustion MUST stop new work and produce a truthful partial
  response when possible.
- **CHAT-FR-133:** Audit records MUST contain structured reason/outcome codes and safe
  argument shapes, not raw arguments or chain-of-thought.
- **CHAT-FR-134:** Observability failure MUST never authorize, repeat, cancel, or
  reinterpret a domain operation.
- **CHAT-FR-135:** Operators MUST be able to diagnose session loss, tool denial,
  validation failure, approval wait, SQL rejection, budget stop, and grounding
  failure.

## 23. Conversation and run state

### 23.1 Conversation state

The existing durable conversation remains authoritative for:

- Owner and project scope.
- Recent messages and compacted summaries.
- Active, background, and paused tasks.
- Requested inputs.
- Prepared actions.
- Current plan reference.
- Pending approvals and child workflow references.
- Effective memory and workspace revision.
- Runtime session mapping.

### 23.2 Turn state

```text
received
  -> claimed
  -> preparing_context
  -> runtime_running
     -> tool_wait
     -> awaiting_user_input
     -> approval_required
     -> response_validation
  -> completed
  -> failed
  -> cancelled
```

The OpenCode session may have richer internal states, but Nexus maps them into this
stable product contract.

### 23.3 Run identity

Each turn records:

- Agent Run ID.
- Conversation/message/turn-claim IDs.
- OpenCode session/message IDs.
- Runtime/model/profile versions.
- Manifest, workspace, and memory hashes.
- Ordered model/tool/activity events.
- Effect/approval/workflow/plan references.
- Evidence and citations.
- Usage, cost, budgets, timings, and stop reason.

## 24. API and event expectations

Exact route names are implementation details, but the product must support:

- Create/list/read conversations.
- Send one idempotent message with optional attachments and project scope.
- Stream a turn with reconnect cursor.
- Cancel the active turn.
- Answer a clarification.
- Approve/reject an exact proposal.
- Read active plans, tasks, workflows, and artifacts.
- Inspect/manage the current user's memory entries and `MEMORY.md` projection.
- Retrieve safe Agent Run details for authorized operators.

Safe event categories include:

- Turn accepted/started/completed/failed/cancelled.
- OpenCode session connected/reconciled/unavailable.
- Tool proposed/validated/started/succeeded/denied/failed.
- SQL validated/rejected/executed.
- Evidence collected and source cards updated.
- Plan/task/workflow transitions.
- Clarification and approval required/resolved.
- Memory proposed/confirmed/updated/deleted.
- Grounding correction and safe fallback.
- Usage/budget warning and stop.

## 25. Security requirements

### 25.1 Isolation

- One workspace and runtime context per conversation/scope revision.
- No cross-user, cross-project, or cross-organization workspace reuse.
- No broad host mounts, home directories, Docker sockets, cloud metadata, or secret
  paths.
- Project source is read-only and snapshot-bound.
- Scratch writes cannot become published artifacts without a governed workflow.

### 25.2 Prompt injection

- System/tool/policy instructions are protected outside untrusted evidence.
- Documents, source files, logs, SQL values, connector results, memory content, and
  attachments are labeled untrusted evidence.
- Tool authority is enforced in code regardless of model instructions.
- Injection tests cover requests to reveal secrets, expand scope, call hidden tools,
  modify memory, execute SQL writes, approve actions, and ignore evidence rules.

### 25.3 Data leakage

- Every read is scoped at execution time, not only manifest time.
- Tool results are sanitized before runtime access.
- Secrets are excluded from prompts, workspaces, events, memory, traces, and final
  responses.
- Model/provider data residency and retention must be organization-configurable.
- Runtime/provider configuration endpoints are not exposed to users or OpenCode.

### 25.4 SQL

- Use AST validation and server-injected scope.
- Use certified views and a least-privilege read-only role.
- No access to user/auth/credential/memory/internal audit tables.
- No database errors containing raw SQL internals in user responses.
- Store normalized safe shapes/hashes for audit rather than sensitive literals.

### 25.5 Memory

- Memory entries inherit tenant/user/project scope.
- No other user may read a user's `MEMORY.md` without explicit admin authority and
  audit.
- Secrets and sensitive personal data are rejected or require a separately governed
  data class.
- Deletion and access revocation propagate to future projections and semantic recall.

## 26. Non-functional requirements

### Reliability

- No duplicate assistant messages, tool effects, approvals, workflow starts, SQL
  executions with external effects, or memory mutations on replay.
- Restart recovery must not require the OpenCode session store.
- Tool/read failure must not corrupt conversation state.
- The current chatbot remains available as rollback until canary completion.

### Performance

- Turn acceptance should occur within two seconds under normal local conditions.
- First safe token or activity should appear within five seconds after OpenCode
  accepts the turn, excluding provider outages.
- Simple direct conversation should avoid tool-manifest or workspace work that is not
  necessary for security/context.
- Read-only tool calls have independent timeouts and bounded outputs.
- Status checks must not repeatedly fetch growing OpenCode transcripts.

### Scalability

- Concurrency ceilings exist at organization, user, conversation, project, runtime,
  provider, connector, and SQL-gateway levels.
- Long conversations use bounded recent history plus durable summaries and memory.
- Tool manifests are filtered to relevant authorized capabilities without semantic
  keyword routing.
- Large workspaces use indexes/search and bounded file reads.

### Portability

- Chat domain models depend on a runtime adapter, not OpenCode HTTP payloads.
- Nexus tool, memory, plan, approval, evidence, and workflow contracts remain usable
  by a future runtime.
- OpenCode version upgrades are deliberate, pinned, and compatibility-tested.

### Accessibility

- Streaming, activity, citations, approvals, memory controls, and attachment status
  are keyboard accessible.
- Status does not depend on color alone.
- Screen readers receive concise stable status, not high-frequency raw events.

### Cost

- Every provider/model route has a certified cost/usage profile.
- Per-turn and aggregate budgets are configurable.
- SQL and structured tools are preferred over retrieving excessive raw logs or
  documents when they can answer safely.
- OpenCode may use compaction, but Nexus records sufficient durable context to audit
  and recover.

## 27. Acceptance scenarios

### 27.1 Tool-free conversation

When the user greets Nexus, discusses architecture, corrects wording, or asks for
general guidance, OpenCode responds naturally with no fake response tool, plan, or
irrelevant task prompt.

### 27.2 RAG question

When an authorized user asks a project documentation question:

- OpenCode selects RAG search/read by meaning.
- Retrieval is project/group scoped.
- The final answer is synthesized by OpenCode.
- Material claims cite returned source evidence.
- No redundant second answer model rewrites the response.

### 27.3 Multi-source incident

Given an error screenshot and a request about last night's failure:

- The image is safely staged and interpreted.
- “Last night” resolves using the user's configured time zone.
- OpenCode queries relevant runtime records and logs.
- It searches documentation/source only when useful.
- The answer distinguishes observed failure evidence from documented behavior.
- Unknown root cause remains explicit if evidence is insufficient.

### 27.4 SQL analysis

Given “Which jobs became slower over the last 30 days?”:

- OpenCode proposes a read-only query over certified views.
- Nexus injects authorized scope and enforces 30 days.
- No system/user/credential table is reachable.
- Results are bounded and evidence-addressable.
- A write, system-catalog, unsafe-function, unbounded, or cross-project query is
  rejected with zero database effect.

### 27.5 Memory

Given an explicit preference request:

- Nexus creates a versioned user-scoped memory entry.
- `MEMORY.md` revision changes.
- A new conversation uses the preference appropriately.
- The preference does not change project access or factual answers.
- Deleting the entry removes it from future projections and recall.

### 27.6 Conflicting correction

When a user correction conflicts with current source/runtime evidence:

- OpenCode does not silently adopt it as truth.
- The response explains the conflict.
- Memory records the statement's confirmation and verification status separately.
- Publication/update requires a governed document workflow.

### 27.7 Documentation delegation

When the user requests project documentation:

- Chat OpenCode starts exactly one Documentation Job.
- The child Job uses the documentation PRD's methodology/depth rules.
- Chat shows progress and does not generate duplicate documents itself.
- Publication remains separate and approval-bound.

### 27.8 Consequential approval

When a user requests workflow cancellation:

- OpenCode identifies one exact workflow.
- Nexus creates one exact pending proposal.
- No cancellation occurs before authorized approval.
- Approval executes exactly once.
- Denial leaves the workflow unchanged.

### 27.9 Restart and replay

If Nexus or OpenCode restarts after reads or a pending approval:

- The turn and child state reconcile from durable Nexus records.
- No read/effect/memory mutation is duplicated incorrectly.
- A replacement session receives bounded context and continues or fails safely.

## 28. Evaluation and promotion gates

### 28.1 Evaluation corpus

Create a versioned multi-turn corpus covering:

- Greetings, discussion, corrections, interruptions, and formatting requests.
- RAG questions, inventories, deep dives, and no-evidence cases.
- Project aliases, case differences, typos, and multi-project scope.
- Runtime health, failures, executions, trends, and SQL analysis.
- Log search and runtime/document contradictions.
- Screenshot and attachment questions.
- Connector/Elasticsearch evidence.
- Memory remember/recall/conflict/delete behavior.
- Long objectives and plan lifecycle.
- Onboarding, documentation, Pack, update, approval, and cancellation actions.
- Denied permissions, unavailable tools, malformed calls, provider failures, budgets,
  restart, reconnect, replay, and concurrent messages.
- Prompt injection through messages, source, documents, logs, memory, SQL values, and
  attachments.

### 28.2 Mandatory safety gates

Promotion fails for any:

- Unauthorized or cross-tenant evidence access.
- SQL write or forbidden-view execution.
- Secret exposure.
- Memory cross-user leakage.
- Direct publication or approval bypass.
- Duplicate consequential effect.
- Tool execution outside the registry/executor.
- Hidden tool exposure.
- Published claim that materially contradicts its cited evidence without disclosure.

### 28.3 Quality gates

- Tool-free turns remain natural and relevant.
- Tool selection is semantically correct across paraphrases and misspellings.
- Multi-tool questions complete useful sequences rather than stopping after the
  first observation.
- RAG factuality and citations meet or exceed the current accepted baseline.
- Runtime and SQL answers preserve scope/time qualification.
- Memory improves continuity without causing irrelevant personalization.
- No active task or plan hijacks unrelated messages.
- Human blind ratings meet the defined relevance, usefulness, clarity, and
  naturalness thresholds.

### 28.4 Operational gates

- Pinned OpenCode compatibility passes direct, tool, multi-tool, streaming,
  cancellation, session-loss, and recovery trials.
- Manifest/handler/schema parity is complete.
- SQL gateway has adversarial parser and database-role proof.
- Memory lifecycle and access-revocation proof passes.
- Shadow and canary runs demonstrate stable latency, budgets, and no effect leakage.
- Old runtime deletion requires separate human approval after the rollback window.

## 29. Migration and rollout

### Stage 0: Freeze current behavior and tool inventory

- Version the current 43-capability registry and dynamic connector catalogue.
- Build the evaluation corpus from real sanitized conversation failures and success
  cases.
- Record current quality, latency, tool selection, grounding, cost, and failure
  baselines.
- Correct capability metadata defects such as read-only plan reads marked reversible.

### Stage 1: OpenCode read-only compatibility spike

- Implement the RAG-side OpenCode Chat adapter using the existing runtime boundary or
  a shared extracted adapter.
- Prove direct answers, RAG search/read, runtime structured reads, logs, connectors,
  tool errors, streaming, cancellation, and session recovery.
- No production route changes and no consequential tools.

### Stage 2: Workspace and `MEMORY.md`

- Add isolated conversation workspaces.
- Compile protected instructions and one per-user `MEMORY.md`.
- Add safe source projections and attachments.
- Add memory read/propose/confirm/supersede/delete tools and UI.

### Stage 3: Guarded runtime SQL

- Create certified runtime analytical views and read-only role.
- Implement AST validation, scope injection, budgets, evidence receipts, and tests.
- Add SQL to the read-only OpenCode cohort.

### Stage 4: Read-only primary canary

- Route permission-scoped users through OpenCode for direct conversation and all read
  tools.
- Remove the legacy answer-generating RAG tool from the OpenCode manifest after
  parity.
- Preserve the old runtime for instant rollback.

### Stage 5: Plans, tasks, and high-level workflows

- Expose durable Agent Plan and conversation-task controls.
- Expose documentation/onboarding/status tools.
- Keep raw workflow primitives internal.
- Prove pause/resume/restart semantics.

### Stage 6: Effects and approvals

- Expose Pack, update, cancellation, memory, and other consequential proposals.
- Enforce one consequential call per response, exact proposals, durable approvals,
  and idempotent resume.

### Stage 7: Unified streaming and UI

- Make OpenCode activity and response streaming the default user experience.
- Add safe tool details, citations, runtime/SQL qualifications, memory disclosures,
  plans, workflow cards, and approval controls.
- Remove user-visible runtime modes.

### Stage 8: Production canary and old-loop retirement

- Shadow, canary, measure, and expand cohorts through signed release gates.
- Retain rollback and schema compatibility.
- Retire the old primary conversational loop only after production evidence and
  explicit human approval.
- Keep useful child LangGraphs as workflow implementations rather than chat brains.

## 30. Rollback strategy

- OpenCode Chat remains feature-flagged through shadow and canary stages.
- Current conversation messages, task/plan/workflow records, approvals, and memory
  remain runtime-neutral.
- Additive events and schemas must be readable by the rollback version or have a
  compatibility projection.
- Turn claims prevent both runtimes from acting on the same message.
- Shadow OpenCode runs execute only explicitly shadow-safe reads in an isolated
  ledger and cannot mutate production state, memory, caches, quotas, tasks, plans,
  workflows, messages, or audits.
- Rollback returns new turns to the old runtime; it does not replay already completed
  OpenCode turns.
- OpenCode session data may be discarded after Nexus state is reconciled.
- Runtime deletion occurs only after a tested signed fallback artifact and human
  approval.

## 31. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OpenCode is optimized for code more than enterprise chat | Awkward dialogue or tool behavior | Real multi-turn corpus, prompt/workspace design, direct-answer gates |
| OpenCode API drift | Integration breaks | Pin version, adapter isolation, compatibility suite, staged upgrades |
| Too many tools overwhelm the model | Wrong tool selection | Permission/state filtering, semantic descriptions, hide low-level primitives, corpus tests |
| RAG answer quality drops when removing inner answer model | Worse documentation answers | Reuse retrieval/reranking, compare factuality/citations, retain transitional fallback |
| SQL is abused or expensive | Data exposure or outage | Curated views, AST validation, scope injection, read-only replica, strict budgets |
| Memory becomes false authority | Persistent misinformation | Confirmation/status, provenance, current evidence precedence, user controls |
| `MEMORY.md` leaks across users | Privacy breach | Per-user workspace, opaque identity, access tests, no shared mounts |
| Session history retains old permissions | Unauthorized context | Scope revision invalidates/replaces session and workspace |
| Source/document prompt injection | Tool or data misuse | Protected instructions and deterministic executor boundaries |
| Runtime session loss breaks continuity | Failed turns | Nexus-owned durable context, bounded handoff, reconciliation |
| Multiple effects in one model response | Partial or duplicate mutations | Execute zero, typed replan feedback, one-call rule |
| OpenCode native tools bypass Nexus | Scope or secret exposure | Restricted workspace, deny shell/network, tool inventory audit |
| Long investigations consume excessive cost | Poor economics | Layered budgets, structured/SQL tools, partial answers, cancellation |
| User cannot understand memory use | Loss of trust | Visible memory management and answer disclosures when material |

## 32. Product success metrics

### Conversation quality

- Direct-answer relevance and naturalness.
- Tool-selection precision and recall by semantic capability family.
- Multi-tool task completion rate.
- Clarification usefulness and echo/repetition rate.
- User acceptance and follow-up correction rate.

### Grounding

- Material claim citation coverage.
- Invalid citation and unsupported-claim rate.
- No-evidence honesty rate.
- Runtime/static conflict disclosure rate.
- SQL scope/time qualification rate.

### Memory

- Explicitly accepted memory updates.
- Relevant memory use versus irrelevant injection.
- Correction/supersession/deletion completion.
- Memory-related factual regressions.
- Cross-scope leakage incidents, with a required value of zero.

### Tooling and operations

- Tool protocol success and typed recovery rate.
- Unauthorized/malformed tool attempts safely denied.
- SQL validation/execution success and timeout rate.
- Session recovery and replay success.
- Duplicate effect incidents, with a required value of zero.
- Latency, model calls, tokens, tool calls, bytes, and cost per turn.

## 33. Resolved product decisions

1. OpenCode is the proposed primary runtime for the entire Nexus chatbot.
2. Nexus will not build or retain a competing semantic planner as the target path.
3. Nexus remains the tool, policy, evidence, workflow, approval, and persistence
   authority.
4. The documentation backend remains a separate high-level capability.
5. OpenCode uses RAG retrieval tools and synthesizes the final chat answer.
6. The legacy RAG answer-generating tool is transitional for OpenCode chat.
7. OpenCode can combine RAG, source, metadata, runtime, logs, SQL, connectors,
   attachments, and memory in one investigation.
8. Runtime SQL is read-only, view-limited, AST-validated, scope-injected, and
   separately budgeted.
9. Every user receives a per-user `MEMORY.md` projection.
10. Nexus structured memory is authoritative; OpenCode cannot update memory by
    editing the file.
11. Hindsight may remain an index/recall layer but cannot promote memory authority.
12. Consequential calls remain sequential and approval-governed.
13. Low-level MCP, source, scan, publication, Pack mutation, and document mutation
    primitives remain internal.
14. General shell, unrestricted network, and broad filesystem access are denied by
    default.
15. Existing durable child workflows may remain even after the chatbot runtime is
    replaced.

## 34. Open implementation decisions

These do not block agreement on the direction, but must be resolved before their
implementation boundary:

1. Whether to deploy one shared OpenCode service pool or isolated runtime processes
   per organization/conversation.
2. Exact OpenCode version and initial certified provider/model route.
3. Whether the RAG-side and documentation-side OpenCode adapters become one shared
   package/service or remain thin adapters over one runtime service.
4. Exact curated runtime analytical view schemas and database isolation strategy.
5. The memory persistence schema, retention defaults, and Hindsight synchronization
   policy.
6. How much recent history versus summary is supplied when recreating a lost session.
7. When bounded shell commands or native OpenCode subagents become eligible for chat.
8. Quantitative canary thresholds derived from the versioned evaluation baseline.

## 35. Definition of done

The initiative is complete when Nexus can route all authorized chatbot turns through
OpenCode and demonstrate that:

1. Direct conversation remains natural and tool-free when appropriate.
2. OpenCode selects and sequences the correct authorized tools across the complete
   product capability surface.
3. RAG, source, metadata, runtime, logs, guarded SQL, connectors, screenshots, and
   memory can be combined into grounded answers.
4. Every user receives an inspectable, correctable, durable `MEMORY.md` projection.
5. Plans, task focus, approvals, and child workflows remain durable across turns and
   restarts.
6. Consequential actions execute only through Nexus policy and exact receipts.
7. Streaming, reconnect, cancellation, replay, and session recovery are reliable.
8. Zero safety-gate failures occur in the release corpus and canary.
9. Quality meets or exceeds the accepted current chatbot baseline.
10. OpenCode-specific details remain behind the runtime adapter and operator views.
11. The previous primary chatbot loop remains rollback-capable through the approved
    observation window.
12. Old-loop deletion occurs only after explicit human approval.
